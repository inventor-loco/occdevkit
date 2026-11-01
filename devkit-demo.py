import time
import multiprocessing as mp
from collections import deque
from math import erfc, sqrt

import numpy as np
import cv2
import matplotlib.pyplot as plt
from picamera2 import Picamera2
from sklearn.mixture import GaussianMixture

plt.switch_backend("Qt5Agg")

font = cv2.FONT_HERSHEY_SIMPLEX

# Process flags
PROCESS_READY = 1
PROCESS_BUSY = 2
PROCESS_SHUTDOWN = 3

# Shared click position inside the AlgorithmProcess
mouse_x = 100
mouse_y = 100


def define_xy(event, x, y, flags, param):
    global mouse_x, mouse_y
    if event == cv2.EVENT_LBUTTONDOWN:
        mouse_x, mouse_y = x, y
        print(f"Selected pixel: x={mouse_x}, y={mouse_y}")


def CameraProcess(pipe, frame_size=(640, 480), fps=30.0, analogue_gain=1.0, exposure_us=100):
    """
    Camera process for Raspberry Pi camera using Picamera2.
    Sends frames on demand through a Pipe.
    """
    picam2 = Picamera2()

    frame_duration_us = int(1_000_000 / fps)

    config = picam2.create_preview_configuration(
        main={"size": frame_size, "format": "RGB888"},
        buffer_count=4
    )
    picam2.configure(config)

    picam2.set_controls({
        "AeEnable": False,
        "AnalogueGain": float(analogue_gain),
        "ExposureTime": int(exposure_us),
        "FrameDurationLimits": (frame_duration_us, frame_duration_us)
    })

    picam2.start()
    time.sleep(0.5)

    try:
        while True:
            msg = pipe.recv()

            if msg == PROCESS_SHUTDOWN:
                pipe.send(None)
                break

            elif msg == PROCESS_READY:
                frame_rgb = picam2.capture_array()
                frame = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
                pipe.send(frame)

            elif msg == PROCESS_BUSY:
                pass

    except KeyboardInterrupt:
        pass
    finally:
        picam2.stop()
        cv2.destroyAllWindows()


def fit_gmm_1d(signal_array, n_components=8):
    """
    Fit a 1D GMM to the signal array.
    Returns fitted model, sorted weights, means, and std devs.
    """
    x = np.asarray(signal_array, dtype=np.float64).reshape(-1, 1)

    if len(x) < n_components * 10:
        return None, None, None, None

    if np.unique(x).size < min(n_components, 4):
        return None, None, None, None

    gmm = GaussianMixture(
        n_components=n_components,
        covariance_type="diag",
        reg_covar=1.0,
        n_init=3,
        random_state=0
    )
    gmm.fit(x)

    means = gmm.means_.flatten()
    variances = gmm.covariances_.flatten()
    stds = np.sqrt(variances)
    weights = gmm.weights_.flatten()

    order = np.argsort(means)
    means = means[order]
    stds = stds[order]
    weights = weights[order]

    return gmm, weights, means, stds


def gaussian_pdf_1d(x, mu, sigma):
    sigma = max(float(sigma), 1e-9)
    return (1.0 / (np.sqrt(2.0 * np.pi) * sigma)) * np.exp(-0.5 * ((x - mu) / sigma) ** 2)


def ml_threshold_between_two_gaussians(mu1, s1, w1, mu2, s2, w2):
    """
    Maximum-likelihood threshold between two 1D Gaussian classes:
        w1*N(x|mu1,s1^2) = w2*N(x|mu2,s2^2)

    Returns the threshold lying between mu1 and mu2 if possible.
    Falls back to midpoint if the analytical solution is numerically awkward.
    """
    mu1, s1, w1 = float(mu1), max(float(s1), 1e-9), max(float(w1), 1e-12)
    mu2, s2, w2 = float(mu2), max(float(s2), 1e-9), max(float(w2), 1e-12)

    # Equal-variance closed form
    if abs(s1 - s2) < 1e-9:
        thr = (
            (mu1 + mu2) / 2.0
            + (s1 ** 2) * np.log(w1 / w2) / (mu2 - mu1)
        )
        return float(np.clip(thr, mu1, mu2))

    # Quadratic form
    # ((x-m2)^2)/(2s2^2) - ((x-m1)^2)/(2s1^2) = ln((w2/s2)/(w1/s1))
    a = (1.0 / (2.0 * s2 ** 2)) - (1.0 / (2.0 * s1 ** 2))
    b = (-mu2 / (s2 ** 2)) + (mu1 / (s1 ** 2))
    c = (mu2 ** 2 / (2.0 * s2 ** 2)) - (mu1 ** 2 / (2.0 * s1 ** 2)) - np.log((w2 / s2) / (w1 / s1))

    roots = np.roots([a, b, c])
    real_roots = [r.real for r in roots if np.isreal(r)]

    # Prefer the root that lies between the means
    candidates = [r for r in real_roots if mu1 <= r <= mu2]
    if candidates:
        return float(candidates[0])

    # Otherwise pick the real root closest to midpoint
    if real_roots:
        midpoint = 0.5 * (mu1 + mu2)
        return float(min(real_roots, key=lambda r: abs(r - midpoint)))

    return 0.5 * (mu1 + mu2)


def compute_ml_thresholds(weights, means, stds):
    thresholds = []
    for i in range(len(means) - 1):
        thr = ml_threshold_between_two_gaussians(
            means[i], stds[i], weights[i],
            means[i + 1], stds[i + 1], weights[i + 1]
        )
        thresholds.append(thr)
    return np.array(thresholds, dtype=np.float64)


def qfunc(x):
    return 0.5 * erfc(x / sqrt(2.0))


def estimate_snr_and_ber(weights, means, stds):
    """
    Estimate SNR and BER from the detected PAM constellation.

    SNR is estimated as:
        signal_power / noise_power

    where:
        signal_power = weighted variance of the symbol means
        noise_power  = weighted average of cluster variances

    BER uses the usual Gray-coded M-PAM approximation:
        BER ≈ [2(M-1)/(M log2(M))] * Q(dmin/(2*sigma_avg))
    """
    weights = np.asarray(weights, dtype=np.float64)
    means = np.asarray(means, dtype=np.float64)
    stds = np.asarray(stds, dtype=np.float64)

    M = len(means)
    if M < 2:
        return None, None

    weights = weights / np.sum(weights)

    mean_global = np.sum(weights * means)
    signal_power = np.sum(weights * (means - mean_global) ** 2)
    noise_power = np.sum(weights * (stds ** 2))

    if noise_power <= 0 or signal_power <= 0:
        return None, None

    snr_linear = signal_power / noise_power
    snr_db = 10.0 * np.log10(snr_linear)

    dmin = np.min(np.diff(means))
    sigma_avg = np.sqrt(noise_power)

    if sigma_avg <= 0 or dmin <= 0:
        return snr_db, None

    ber = (2.0 * (M - 1) / (M * np.log2(M))) * qfunc(dmin / (2.0 * sigma_avg))
    return snr_db, ber


def AlgorithmProcess(
    pipe,
    mainPipe,
    fps=30.0,
    live_buffer_size=600,
    plot_every_n_frames=3,
    gmm_components=8
):
    """
    Receives frames, displays image + live waveform + live histogram + GMM fit,
    allows user to click pixel, and updates the signal in real time.
    """
    global mouse_x, mouse_y

    live_signal = deque([0] * live_buffer_size, maxlen=live_buffer_size)
    live_time = np.arange(live_buffer_size) / fps

    frame_counter = 0
    last_status_text = "SNR(dB) = --.--\tBER = --.--"

    plt.ion()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Waveform (left)
    line1, = ax1.plot(live_time, list(live_signal), ".-", label="signal")
    ax1.set_xlabel("Time in live window (s)")
    ax1.set_ylabel("Selected pixel intensity")
    ax1.set_title("Live pixel waveform")
    ax1.set_ylim(0, 255)
    ax1.grid(True)

    # Horizontal threshold lines on waveform
    threshold_lines = []

    # Histogram (right), rotated: x=count, y=intensity
    hist_bins = np.arange(257)  # integer bins 0..255
    hist_counts, hist_edges = np.histogram(list(live_signal), bins=hist_bins)
    bar_container = ax2.barh(
        hist_edges[:-1],
        hist_counts,
        height=1.0,
        align="edge",
        alpha=0.6
    )
    ax2.set_ylim(0, 255)
    ax2.set_xlabel("Count")
    ax2.set_ylabel("Pixel intensity")
    ax2.set_title("Live histogram + 8-GMM fit")
    ax2.grid(True)

    x_pdf = np.linspace(0, 255, 1024).reshape(-1, 1)
    gmm_line, = ax2.plot([], [], linewidth=2, label="GMM pdf")
    mean_lines = []
    info_text = ax2.text(
        0.02, 0.98, "", transform=ax2.transAxes,
        va="top", ha="left",
        bbox=dict(facecolor="white", alpha=0.7, edgecolor="none")
    )
    ax2.legend(loc="upper right")

    fig.tight_layout()

    cv2.namedWindow("Camera", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("Camera", define_xy)

    try:
        while True:
            pipe.send(PROCESS_READY)
            frame = pipe.recv()

            if frame is None:
                print("AlgorithmProcess: shutdown received from camera process")
                mainPipe.send(PROCESS_SHUTDOWN)
                break

            rows, cols, _ = frame.shape

            x = int(np.clip(mouse_x, 0, cols - 1))
            y = int(np.clip(mouse_y, 0, rows - 1))

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            px_val = int(gray[y, x])

            live_signal.append(px_val)

            if frame_counter % plot_every_n_frames == 0:
                signal_list = np.array(live_signal, dtype=np.float64)

                # Waveform
                line1.set_ydata(signal_list)

                # Histogram
                hist_counts, _ = np.histogram(signal_list, bins=hist_bins)
                for rect, h in zip(bar_container, hist_counts):
                    rect.set_width(h)

                x_max = max(10, np.max(hist_counts) * 1.15)
                ax2.set_xlim(0, x_max)

                # Remove old mean lines
                for ln in mean_lines:
                    ln.remove()
                mean_lines.clear()

                # Remove old threshold lines
                for ln in threshold_lines:
                    ln.remove()
                threshold_lines.clear()

                # Fit GMM
                gmm, weights, means, stds = fit_gmm_1d(signal_list, n_components=gmm_components)

                if gmm is not None:
                    log_pdf = gmm.score_samples(x_pdf)
                    pdf = np.exp(log_pdf)

                    # Scale pdf to histogram counts
                    scaled_pdf = pdf * len(signal_list)
                    gmm_line.set_data(scaled_pdf, x_pdf.flatten())

                    # Draw mean markers on histogram
                    for m in means:
                        ln = ax2.axhline(m, linestyle="--", linewidth=1)
                        mean_lines.append(ln)

                    # Compute ML thresholds and draw them on waveform
                    thresholds = compute_ml_thresholds(weights, means, stds)
                    for thr in thresholds:
                        ln = ax1.axhline(thr, color="red", linestyle="--", linewidth=1.2)
                        threshold_lines.append(ln)

                    # SNR and BER
                    snr_db, ber = estimate_snr_and_ber(weights, means, stds)

                    if snr_db is not None and ber is not None:
                        if ber < 1e-2:
                            last_status_text = f"SNR(dB) = {snr_db:.2f}\tBER = {ber:.2e}"
                        else:
                            last_status_text = f"SNR(dB) = {snr_db:.2f}\tBER = {ber:.2f}"
                    elif snr_db is not None:
                        last_status_text = f"SNR(dB) = {snr_db:.2f}\tBER = --.--"
                    else:
                        last_status_text = "SNR(dB) = --.--\tBER = --.--"

                    mean_str = ", ".join([f"{m:.1f}" for m in means])
                    thr_str = ", ".join([f"{t:.1f}" for t in thresholds])
                    info_text.set_text(f"GMM means:\n{mean_str}\nML thr:\n{thr_str}")

                else:
                    gmm_line.set_data([], [])
                    info_text.set_text("GMM: waiting for enough variation/data")
                    last_status_text = "SNR(dB) = --.--\tBER = --.--"

                fig.canvas.draw_idle()
                fig.canvas.flush_events()
                plt.pause(0.001)

                print(last_status_text)

            cv2.line(frame, (x, 0), (x, rows), (0, 255, 0), 1)
            cv2.line(frame, (0, y), (cols, y), (0, 255, 0), 1)
            cv2.circle(frame, (x, y), 6, (0, 0, 255), 1)

            cv2.putText(frame, f"x={x}, y={y}", (20, 30), font, 0.7, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.putText(frame, f"Pixel={px_val}", (20, 60), font, 0.7, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.putText(frame, "Click to select pixel | ESC to finish", (20, 90), font, 0.6, (0, 255, 0), 1, cv2.LINE_AA)

            cv2.imshow("Camera", frame)

            frame_counter += 1

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                print("AlgorithmProcess: EXIT REQUEST")
                try:
                    pipe.send(PROCESS_SHUTDOWN)
                except Exception:
                    pass
                mainPipe.send(PROCESS_SHUTDOWN)
                break

    finally:
        cv2.destroyAllWindows()
        plt.ioff()
        plt.close(fig)


if __name__ == "__main__":
    mp.set_start_method("spawn", force=True)

    fps = 30.0
    live_buffer_size = 600      # ~20 s at 30 fps
    frame_size = (640, 480)
    exposure_us = 100
    analogue_gain = 1.0

    AlgorithmSide, CameraSide = mp.Pipe()
    MainSide, AlgorithmToMainSide = mp.Pipe()

    p1 = mp.Process(
        target=CameraProcess,
        args=(AlgorithmSide, frame_size, fps, analogue_gain, exposure_us)
    )
    p2 = mp.Process(
        target=AlgorithmProcess,
        args=(CameraSide, AlgorithmToMainSide, fps, live_buffer_size, 3, 8)
    )

    p1.start()
    p2.start()

    try:
        while True:
            if MainSide.recv() == PROCESS_SHUTDOWN:
                print("Main Process: EXIT REQUEST")
                break
    finally:
        p1.join()
        p2.join()
        print("Processes finished")
