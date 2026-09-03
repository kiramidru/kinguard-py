import os
import time
import urllib.request
import logging
import cv2
import mediapipe as mp

# Configure structured console logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("KinguardAI")

# Download the required model bundle for the modern Tasks API if not present
MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task"
MODEL_PATH = "pose_landmarker.task"

if not os.path.exists(MODEL_PATH):
    logger.info("Downloading MediaPipe Pose Landmarker model...")
    urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
    logger.info("Model download complete.")

# Initialize MediaPipe Tasks PoseLandmarker
BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

options = PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path=MODEL_PATH),
    running_mode=VisionRunningMode.VIDEO,
    num_poses=1,
)

cap = cv2.VideoCapture(0)
fall_start_time = None
FALL_THRESHOLD_SECONDS = 2.0

logger.info("Vision monitoring active. Press Ctrl+C to stop.")

last_logged_status = ""

with PoseLandmarker.create_from_options(options) as landmarker:
    try:
        while cap.isOpened():
            success, frame = cap.read()
            if not success:
                logger.error("Camera feed unavailable or frame capture failed.")
                break

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

            timestamp_ms = int(time.time() * 1000)
            detection_result = landmarker.detect_for_video(mp_image, timestamp_ms)

            status_text = "Monitoring Upright"

            if detection_result.pose_landmarks:
                for landmarks in detection_result.pose_landmarks:
                    left_shoulder = landmarks[11]
                    right_shoulder = landmarks[12]
                    left_hip = landmarks[23]
                    right_hip = landmarks[24]

                    shoulder_y = (left_shoulder.y + right_shoulder.y) / 2
                    hip_y = (left_hip.y + right_hip.y) / 2
                    shoulder_x = (left_shoulder.x + right_shoulder.x) / 2
                    hip_x = (left_hip.x + right_hip.x) / 2

                    is_horizontal = abs(shoulder_x - hip_x) > abs(shoulder_y - hip_y)

                    if is_horizontal:
                        if fall_start_time is None:
                            fall_start_time = time.time()
                            logger.warning(
                                "Horizontal orientation detected. Tracking duration..."
                            )
                        elif time.time() - fall_start_time > FALL_THRESHOLD_SECONDS:
                            status_text = "ALERT: FALL DETECTED!"
                            logger.critical(
                                "FALL CONFIRMED! Saving snapshot evidence..."
                            )
                            cv2.imwrite("fall_incident.jpg", frame)
                        else:
                            status_text = "Warning: Possible Fall..."
                    else:
                        if fall_start_time is not None:
                            logger.info("Subject recovered upright posture.")
                        fall_start_time = None

            # Log status updates only when they transition
            if status_text != last_logged_status:
                if "ALERT" in status_text:
                    logger.error(status_text)
                elif "Warning" in status_text:
                    logger.warning(status_text)
                else:
                    logger.info(status_text)
                last_logged_status = status_text

            time.sleep(0.03)

    except KeyboardInterrupt:
        logger.info("Shutting down monitoring service gracefully.")
    finally:

        cap.release()
