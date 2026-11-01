# Optical Camera Communication Development Kit (OCC devkit)

Tools for building OCC networks using off-the-shelf open-source hardware.

The current release is a live OCC demo for a Raspberry Pi 5 with the Camera Module 3. You click a pixel in the camera view and the demo plots that pixel's intensity over time. It then looks through the received signal buffer for clusters of pulse amplitude modulation (PAM) symbols and estimates the link quality as signal-to-noise ratio (SNR) and bit error rate (BER).

## Hardware

- Raspberry Pi 5 (4 GB or more recommended)
- Raspberry Pi Camera Module 3, connected to either CSI port with the Pi 5 camera cable
- microSD card with **Raspberry Pi OS (64-bit) with desktop**, Bookworm or newer. The Lite image has no desktop, so the demo windows cannot open.
- An optical transmitter, for example an LED driven with a PAM signal

## Installation

1. Flash Raspberry Pi OS with desktop using [Raspberry Pi Imager](https://www.raspberrypi.com/software/). In the Imager settings, set the username, Wi-Fi and enable SSH so you can reach the Pi without a monitor.
2. With the Pi powered off, connect the camera, then boot it.
3. Open a terminal on the Pi (over SSH or on the desktop) and run:

   ```bash
   git clone https://github.com/inventor-loco/occdevkit.git
   cd occdevkit
   ./install.sh
   sudo reboot
   ```

`install.sh` does the following:

- Installs the Python dependencies (`picamera2`, OpenCV, NumPy, Matplotlib, PyQt5, scikit-learn) and `rpicam-apps` with `apt`.
- Enables the VNC server. Pass `--skip-vnc` if you use a monitor and keyboard instead.
- Checks that every Python module imports and that a camera is detected.

All dependencies come from `apt`, not `pip`. `picamera2` must match the system's libcamera build, and pip's `opencv-python` wheels conflict with the system PyQt5. If you work in a virtual environment, create it with `python3 -m venv --system-site-packages` so it can see these packages.

## Connecting with VNC Viewer

1. Install [RealVNC Viewer](https://www.realvnc.com/en/connect/download/viewer/) on your computer.
2. Connect to `raspberrypi.local`, or the hostname or IP address you set in Imager, and log in with your Pi username and password.
3. If the Pi has no monitor attached and the desktop looks tiny or does not appear, set a resolution with `sudo raspi-config` → *Display Options* → *VNC Resolution*, then reboot.

## Running the demo

In a terminal **on the Pi desktop** (inside the VNC session), run:

```bash
cd ~/occdevkit
python3 devkit-demo.py
```

The terminal has to belong to the desktop session so the windows can open. If you start the demo from an SSH session instead, run `export DISPLAY=:0` first and the windows will appear in the VNC desktop.

Two windows open:

- **Camera**: the live image. Click a pixel to select it. The green crosshair marks the selected pixel and its current intensity.
- **Plots**: on the left, the selected pixel's waveform over the last ~20 s, with the maximum-likelihood decision thresholds drawn in red. On the right, an intensity histogram with a Gaussian mixture fit (one Gaussian per PAM level, set by `pam_order`) and the estimated symbol levels.

The terminal prints the SNR and BER estimates as the plots update. Press **ESC** in the Camera window to quit.

## Configuration

The demo's parameters are set at the bottom of [`devkit-demo.py`](devkit-demo.py), in the `if __name__ == "__main__":` block:

| Parameter          | Default      | Meaning                                                     |
| ------------------ | ------------ | ----------------------------------------------------------- |
| `fps`              | `30.0`       | Camera frame rate (fixed, auto exposure disabled)           |
| `frame_size`       | `(640, 480)` | Capture resolution                                          |
| `exposure_us`      | `100`        | Exposure time in microseconds                               |
| `analogue_gain`    | `1.0`        | Sensor analogue gain                                        |
| `live_buffer_size` | `600`        | Number of samples kept in the live window (~20 s at 30 fps) |
| `pam_order`        | `8`          | M of the M-PAM signal: number of Gaussians fitted           |
| `plot_every_n_frames` | `3`       | How often the plots and SNR/BER estimates refresh           |

The short default exposure keeps the background dark so that the transmitter stands out. Raise `exposure_us` or `analogue_gain` if the image is too dark to find the LED.

Set `pam_order` to the number of intensity levels your transmitter uses (for example 2 for OOK, 4 for 4-PAM). The BER estimate assumes Gray coding, so it is most meaningful when M is a power of 2.

## Troubleshooting

- **`no camera detected` or `IndexError` from Picamera2**: power off the Pi, reseat the ribbon cable on both ends (contacts facing the right way), then boot again. `rpicam-hello --list-cameras` should list an `imx708` sensor.
- **`cannot connect to X server` / `could not connect to display`**: the demo was started outside the desktop session. Use a terminal inside VNC, or run `export DISPLAY=:0` first.
- **`Could not load the Qt platform plugin`**: usually caused by a pip-installed `opencv-python`. Remove it with `pip uninstall opencv-python` and rely on the `apt` package.
- **`ModuleNotFoundError: No module named 'picamera2'`**: you are running a virtual environment created without `--system-site-packages`. Use `/usr/bin/python3` or recreate the environment.
- **Camera busy**: only one program can use the camera at a time. Close any other camera app (or an earlier run of the demo) and try again.

## License

GNU General Public License v3.0. See [LICENSE](LICENSE).
