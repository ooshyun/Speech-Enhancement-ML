import tensorflow as tf
device_name = tf.test.gpu_device_name()
if device_name != '/device:GPU:0':
  raise SystemError('GPU device not found')
print('Found GPU at: {}'.format(device_name))

import librosa
import scipy
import time
import datetime

import pandas as pd
import os
import matplotlib.pyplot as plt
import numpy as np
import tensorflow as tf
import IPython.display as ipd

import glob
import numpy as np
from data_processing.feature_extractor import FeatureExtractor
from utils import prepare_input_features
from model import build_model, build_model_lstm
# Load the TensorBoard notebook extension.
# %load_ext tensorboard

from tensorflow.python.client import device_lib
import keras.models
from pathlib import Path

device_lib.list_local_devices()

tf.random.set_seed(999)
np.random.seed(999)

# model_name = 'cnn'
model_name = 'lstm'

# domain = 'freq' # Not implemented
domain = 'time'

if model_name == 'lstm':
    if domain == 'time':
        path_to_dataset = f"./records_{model_name}_{domain}"
    else:    
        path_to_dataset = f"./records_{model_name}"
else:
    path_to_dataset = f"./records_{model_name}"

# get training and validation tf record file names
train_tfrecords_filenames = glob.glob(os.path.join(path_to_dataset, 'train_*'))
val_tfrecords_filenames = glob.glob(os.path.join(path_to_dataset, 'val_*'))

# suffle the file names for training
np.random.shuffle(train_tfrecords_filenames)
print("Training file names: ", train_tfrecords_filenames)
print("Validation file names: ", val_tfrecords_filenames)

if model_name == "cnn":
    win_length = 256
    overlap      = round(0.25 * win_length) # overlap of 75%
    n_fft    = win_length
    inputFs      = 48e3
    fs           = 16e3
    numFeatures  = n_fft//2 + 1
    numSegments  = 8

elif model_name == "lstm":
    win_length = 512
    overlap      = round(0.5 * win_length) # overlap of 50%
    n_fft    = win_length
    inputFs      = 48e3
    fs           = 16e3
    numFeatures  = n_fft//2 + 1
    numSegments  = 62 # 1.008 sec in 512 window, 256 hop, sr = 16000 Hz

else:
    NotImplementedError("Only implemented cnn and lstm")

print("-"*30)
print("win_length:",win_length)
print("overlap:",overlap)
print("n_fft:",n_fft)
print("inputFs:",inputFs)
print("fs:",fs)
print("numFeatures:",numFeatures)
print("numSegments:",numSegments)
print("-"*30)

def stft_tensorflow(wav):
    window_fn = tf.signal.hamming_window
    wav_stft = tf.signal.stft(wav, frame_length=n_fft, frame_step=overlap, window_fn=window_fn)
    
    # if using inverse stft, 
    # inverse_stft = tf.signal.inverse_stft(
    #   wav_stft, frame_length=n_fft, frame_step=overlap,
    #   window_fn=tf.signal.inverse_stft_window_fn(
    #      frame_step=overlap, forward_window_fn=window_fn))
    
    print("DEBUG", wav_stft.shape, wav_stft.dtype)

    # [TODO] Phase aware process
    wav_stft_mag_features = tf.abs(wav_stft)
    wav_stft_phase = tf.experimental.numpy.angle(wav_stft)
    wav_stft_real = tf.math.real(wav_stft)
    wav_stft_imag = tf.math.imag(wav_stft)

    print("DEBUG", wav_stft_mag_features.shape, wav_stft_phase.shape, wav_stft_real.shape, wav_stft_imag.shape)
    print("DEBUG", wav_stft_mag_features.dtype, wav_stft_phase.dtype, wav_stft_real.dtype, wav_stft_imag.dtype)

    return wav_stft_mag_features, wav_stft_phase, wav_stft_real, wav_stft_imag


def tf_record_parser(record):
    if model_name == "cnn":
        keys_to_features = {
        "noise_stft_phase": tf.io.FixedLenFeature((), tf.string, default_value=""),
        'noise_stft_mag_features': tf.io.FixedLenFeature([], tf.string),
        "clean_stft_magnitude": tf.io.FixedLenFeature((), tf.string)
        }
        features = tf.io.parse_single_example(record, keys_to_features)

        noise_stft_mag_features = tf.io.decode_raw(features['noise_stft_mag_features'], tf.float32)
        clean_stft_magnitude = tf.io.decode_raw(features['clean_stft_magnitude'], tf.float32)
        noise_stft_phase = tf.io.decode_raw(features['noise_stft_phase'], tf.float32)

        # reshape input and annotation images, cnn
        noise_stft_mag_features = tf.reshape(noise_stft_mag_features, (numFeatures, numSegments, 1), name="noise_stft_mag_features")
        clean_stft_magnitude = tf.reshape(clean_stft_magnitude, (numFeatures, 1, 1), name="clean_stft_magnitude") # [TODO] Chekc
        noise_stft_phase = tf.reshape(noise_stft_phase, (numFeatures,), name="noise_stft_phase")
    
        noisy_stft = tf.stack([noise_stft_mag_features, noise_stft_phase])
        clean_stft = tf.stack([clean_stft_magnitude, clean_stft_phase])

        return noise_stft_mag_features , clean_stft_magnitude
    elif model_name == 'lstm':
        if domain == 'time':    
            keys_to_features = {
            "noisy": tf.io.FixedLenFeature((), tf.string, default_value=""),
            'clean': tf.io.FixedLenFeature([], tf.string),
            }

            features = tf.io.parse_single_example(record, keys_to_features)

            noisy = tf.io.decode_raw(features['noisy'], tf.float32)
            clean = tf.io.decode_raw(features['clean'], tf.float32)
            
            noise_stft_magnitude, noise_stft_phase, noise_stft_real, noise_stft_imag = stft_tensorflow(noisy)
            clean_stft_magnitude, clean_stft_phase, clean_stft_real, clean_stft_imag = stft_tensorflow(clean)
        else:
            keys_to_features = {
                "noise_stft_phase": tf.io.FixedLenFeature((), tf.string, default_value=""),
                'noise_stft_mag_features': tf.io.FixedLenFeature([], tf.string),
                "clean_stft_phase": tf.io.FixedLenFeature((), tf.string),
                "clean_stft_magnitude": tf.io.FixedLenFeature((), tf.string)
            }
            features = tf.io.parse_single_example(record, keys_to_features)

            noise_stft_mag_features = tf.io.decode_raw(features['noise_stft_mag_features'], tf.float32)
            clean_stft_magnitude = tf.io.decode_raw(features['clean_stft_magnitude'], tf.float32)
            noise_stft_phase = tf.io.decode_raw(features['noise_stft_phase'], tf.float32)
            clean_stft_phase = tf.io.decode_raw(features['clean_stft_phase'], tf.float32)

        # reshape input and annotation images, lstm
        noise_stft_real = tf.reshape(noise_stft_real, (1, numSegments, numFeatures), name="noise_stft_real")
        clean_stft_real = tf.reshape(clean_stft_real, (1, numSegments, numFeatures), name="clean_stft_real")

        noise_stft_imag = tf.reshape(noise_stft_imag, (1, numSegments, numFeatures), name="noise_stft_imag")
        clean_stft_imag = tf.reshape(clean_stft_imag, (1, numSegments, numFeatures), name="clean_stft_imag")    

        noisy_feature = tf.stack([noise_stft_real, noise_stft_imag], name="noisy")
        clean_feature = tf.stack([clean_stft_real, clean_stft_imag], name="clean")

        return noisy_feature, clean_feature
    else:
        raise ValueError("Model didn't implement...")

train_dataset = tf.data.TFRecordDataset([train_tfrecords_filenames])
train_dataset = train_dataset.map(tf_record_parser)
# train_dataset = train_dataset.map(lambda file: tf.py_function(func=tf_record_parser, inp=file, Tout=tf.float32))

train_dataset = train_dataset.shuffle(8192)
train_dataset = train_dataset.repeat()
train_dataset = train_dataset.batch(512)
train_dataset = train_dataset.prefetch(buffer_size=tf.data.experimental.AUTOTUNE)

# val_dataset

test_dataset = tf.data.TFRecordDataset([val_tfrecords_filenames])
test_dataset = test_dataset.map(tf_record_parser)
test_dataset = test_dataset.repeat(1)
test_dataset = test_dataset.batch(512)

if model_name == "cnn":
    model = build_model(l2_strength=0.0)
elif model_name == "lstm":
    model = build_model_lstm()
else:
    raise ValueError("Model didn't implement...")
model.summary()

# You might need to install the following dependencies: sudo apt install python-pydot python-pydot-ng graphviz
# tf.keras.utils.plot_model(model, show_shapes=True, dpi=64)

baseline_val_loss = model.evaluate(test_dataset)[0]
print(f"Baseline accuracy {baseline_val_loss}")

class TimeHistory(tf.keras.callbacks.Callback):
    def __init__(self, filepath):
        super(TimeHistory, self).__init__()
        self.filepath = filepath

    def on_train_begin(self, logs={}):
        self.epoch_times = []
        self.batch_times = []
        self.train_time = []
        self.train_time.append(time.perf_counter())

    def on_batch_begin(self, batch, logs={}):
        self.batch_time_start = time.perf_counter()

    def on_batch_end(self, batch, logs={}):
        self.batch_times.append(time.perf_counter() - self.batch_time_start)

    def on_epoch_begin(self, epoch, logs={}):
        self.epoch_time_start = time.perf_counter()

    def on_epoch_end(self, epoch, logs={}):
        self.epoch_times.append(time.perf_counter() - self.epoch_time_start)

    def on_train_end(self, logs={}):
        self.train_time.append(time.perf_counter())

        log_file_path = self.filepath
        with open(log_file_path, "w") as tmp:
            tmp.write(f"  Total time\n")
            tmp.write(f"start    : {time_callback.train_time[0]} sec\n")
            tmp.write(f"end      : {time_callback.train_time[1]} sec\n")
            tmp.write(f"duration : {time_callback.train_time[1]- time_callback.train_time[0]} sec\n")
            
            tmp.write(f"  Epoch time, {len(time_callback.epoch_times)}\n")
            for epoch, t in enumerate(time_callback.epoch_times):
                tmp.write(f"{epoch} : {t}\n")

            tmp.write(f"  Batch time, {len(time_callback.batch_times)}\n")
            
            for num, t in enumerate(time_callback.batch_times):
                tmp.write(f"{t} ")
                if num % 100 == 99:
                    tmp.write(f"\n")


save_path = os.path.join(f"./result/{model_name}", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))

checkpoint_save_path = os.path.join(save_path, "checkpoint/model-{epoch:02d}-{val_loss:.4f}.hdf5")
model_save_path = os.path.join(save_path, "model")
console_log_save_path = os.path.join(save_path, "debug.txt")

early_stopping_callback = tf.keras.callbacks.EarlyStopping(monitor='val_loss', patience=50, restore_best_weights=True, baseline=None)
logdir = os.path.join(f"./logs/{model_name}", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
tensorboard_callback = tf.keras.callbacks.TensorBoard(logdir, update_freq='batch', histogram_freq=1, write_graph=True)

# histogram_freq=0, write_graph=True: for monitoring the weight histogram

checkpoint_callback = tf.keras.callbacks.ModelCheckpoint(filepath=checkpoint_save_path, 
                                                         test='val_loss', save_best_only=True)
time_callback = TimeHistory(filepath=console_log_save_path)

model.fit(train_dataset,
         steps_per_epoch=5, # you might need to change this
         validation_data=test_dataset,
         epochs=1,
         callbacks=[early_stopping_callback, tensorboard_callback, checkpoint_callback, time_callback]
        )

val_loss = model.evaluate(test_dataset)[0]
if val_loss < baseline_val_loss:
  print("New model saved.")
  keras.models.save_model(model, model_save_path, overwrite=True, include_optimizer=True)
  # model.save('./denoiser_cnn_log_mel_generator.h5')