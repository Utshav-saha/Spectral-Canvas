from scipy.io.wavfile import read
import matplotlib.pyplot as plt
import numpy as np

fs, audio = read("black1.wav")

time = np.arange(len(audio)) / fs

plt.plot(time, audio)
plt.xlabel("Time (s)")
plt.ylabel("Amplitude")
plt.show()