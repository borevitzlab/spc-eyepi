import datetime
import logging.config
import os
import shutil
import time
import tempfile
import numpy
import requests
import traceback
from dateutil import zoneinfo, parser
from libs.CryptUtil import SSHManager
from requests.auth import HTTPBasicAuth, HTTPDigestAuth
from xml.etree import ElementTree
from collections import deque
from io import BytesIO
import threading
from threading import Thread, Event, Lock
from libs.SysUtil import SysUtil
import paho.mqtt.client as client
from paho.mqtt.publish import single
from libs.SysUtil import recursive_update
import json
import cv2
from zlib import crc32
import yaml

timezone = zoneinfo.get_zonefile_instance().get("Australia/Canberra")


try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

try:
    import gphoto2cffi as gp
except Exception as e:
    logging.error("Couldnt import gphoto2-cffi module, no DSLR support: {}".format(str(e)))

try:
    import picamera
    import picamera.array
except Exception as e:
    logging.error("Couldnt import picamera module, no picamera camera support: {}".format(str(e)))
    pass

try:
    import telegraf
except Exception as e:
    logging.error("Couldnt import pytelegraf module, no telemetry: {}".format(str(e)))


class TwentyFourHourTimeParserInfo(parser.parserinfo):
    def validate(self, res):
        if res.year is not None:
            time = str(res.year)
            res.year = None
            res.hour = int(time[:2])
            res.minute = int(time[2:])
        if res.tzoffset == 0 and not res.tzname or res.tzname == 'Z':
            res.tzname = "UTC"
            res.tzoffset = 0
        elif res.tzoffset != 0 and res.tzname and self.utczone(res.tzname):
            res.tzoffset = 0
        return True


USBDEVFS_RESET = 21780


def nested_lookup(key, document):
    """
    nested document lookup,
    works on dicts and lists

    :param key: string of key to lookup
    :param document: dict or list to lookup
    :return: yields item
    """
    if isinstance(document, list):
        for d in document:
            for result in nested_lookup(key, d):
                yield result

    if isinstance(document, dict):
        for k, v in document.items():
            if k == key:
                yield v
            elif isinstance(v, dict):
                for result in nested_lookup(key, v):
                    yield result
            elif isinstance(v, list):
                for d in v:
                    for result in nested_lookup(key, d):
                        yield result


class Camera(Thread):
    """
    Base Camera class.

    :cvar int accuracy: 3: Number of seconds caputre should be accurate to.
    :cvar int default_width: 1080: Default width of resized images.
    :cvar int default_height: 720: Default height of resuzed images.
    :cvar list file_types: ["CR2", "RAW", "NEF", "JPG", "JPEG", "PPM", "TIF", "TIFF"]: Supported output image types.
    :cvar list output_types: ["tif", "jpg"]: Output image types, ignored by GPCamera.

    :ivar collections.deque communication_queue: Reference to a deque, or a deque.
    :ivar logging.Logger logger: Logger for each Camera.
    :ivar threading.Event stopper: Stopper event object to allow thread stopping.
    :ivar str identifier: Unique identifier for the camera. Used to distinguish cameras from one another.
    :ivar list failed: List of failed capture timepoints.
    :ivar str config_filename: Confuguration file path, unused if camera is instantiated with the noconf init parameter.
    :ivar str camera_name: Human friendly name of the camera.
    :ivar int interval: Capture interval (in seconds).
    :ivar str spool_directory: Path to stream images into during the capture process.
    :ivar str upload_directory: Path to move images to after the captre process.
    :ivar datetime.time begin_capture: Naive start time for capture.
    :ivar datetime.time end_capture: Naive end time for capture.
    :ivar datetime.datetime current_capture_time: When the capture process began.
    """

    accuracy = 3
    default_width, default_height = 1080, 720
    file_types = ["CR2", "RAW", "NEF", "JPG", "JPEG", "PPM", "TIF", "TIFF"]
    output_types = ["tif", 'jpg']

    _frame = None
    _thread = None
    _last_access = None

    def init_stream(self):
        """
        Initialises a video stream class thread.
        """
        if self.__class__._thread is None:
            # start background frame thread
            self.__class__._thread = threading.Thread(target=self.stream_thread)
            self.__class__._thread.start()
            # wait until frames start to be available
            while self.__class__._frame is None:
                time.sleep(0.01)

    def get_frame(self) -> bytes:
        """
        Gets a frame from the a running :func:`stream_thread`.

        :return: encoded image data as bytes.
        """
        self.__class__._last_access = time.time()
        self.init_stream()
        return self.__class__._frame

    @classmethod
    def stream_thread(cls):
        """
        Boilerplate stream thread.
        Override this with the correct method of opening the camera, grabbing image data and closing the camera.
        """
        print("Unimplemented classmethod call: stream_thread")
        print("You should not create a Camera object directly")

        def get_camera():
            pass

        with get_camera() as camera:
            # let camera warm up
            while True:
                # example, you actually need to get the data from somewhere.
                cls._frame = camera.get_frame().read()
                # if there hasn't been any clients asking for frames in
                # the last 10 seconds stop the thread
                if time.time() - cls._last_access > 10:
                    break
        cls._thread = None

    def __init__(self, identifier: str, config: dict = None, queue: deque = None,
                 noconf: bool = False,
                 **kwargs):
        """
        Initialiser for cameras...

        :param identifier: unique identified for this camera, MANDATORY
        :param config: Configuration section for this camera.
        :param queue: deque to push info into
        :param noconf: dont create a config, or watch anything. Used for temporarily streaming from a camera
        :param kwargs:
        """
        super().__init__(name=identifier)
        print("Thread started {}: {}".format(self.__class__, identifier))
        if queue is None:
            queue = deque(tuple(), 256)
        self.communication_queue = queue

        self.logger = logging.getLogger(identifier)
        self.logger.info("init...")

        self.stopper = Event()
        self.identifier = identifier
        self.name = identifier
        self.failed = list()
        self._exif = dict()
        self.focus_position = None
        self._frame = None
        self._image = numpy.empty((Camera.default_width, Camera.default_height, 3), numpy.uint8)
        self.config = dict()
        if config is not None:
            self.config = config.copy()
        self.name = self.config.get("name", identifier)

        self.interval = int(self.config.get("interval", 300))
        self.upload_directory = "/home/images/{}".format(str(self.identifier))
        try:
            self.upload_directory = self.config["output_dir"]
        except:
            pass
        self.spool_directory = None
        if self.config.get("disable_ram_spooling", False):
            self.spool_directory = tempfile.mkdtemp(prefix="SPC-EYEPI")

        self.begin_capture = datetime.time(0, 0)
        self.end_capture = datetime.time(23, 59)

        try:
            self.begin_capture = parser.parse(str(self.config["starttime"]),
                                              parserinfo=TwentyFourHourTimeParserInfo()).time()
        except Exception as e:
            self.logger.error("Time conversion error starttime - {}".format(str(e)))
        try:
            # cut string to max of 4.
            self.end_capture = parser.parse(str(self.config["stoptime"]),
                                            parserinfo=TwentyFourHourTimeParserInfo()).time()
        except Exception as e:
            self.logger.error("Time conversion error stoptime - {}".format(str(e)))

        try:
            if not os.path.exists(self.upload_directory):
                self.logger.info("Creating local output dir {}".format(self.upload_directory))
                os.makedirs(self.upload_directory)
        except Exception as e:
            self.logger.error("Creating directories {}".format(str(e)))

        self._exif = self.get_exif_fields()

        self.logger.info("Capturing from {} to {}".format(self.begin_capture.strftime("%H:%M"),
                                                          self.end_capture.strftime("%H:%M")))
        self.logger.info("Interval: {}".format(self.interval))

        self.current_capture_time = datetime.datetime.now()
        self.setupmqtt()

    def set_config(self, pdict):

        default_conf = {
            'name': self.identifier,
            "capture": True,
            'interval': 300,
            'starttime': "5AM",
            'stoptime': "10PM",
            'resize': True,
            'output_dir': "/home/images/{}".format(self.identifier)
        }
        global_conf = yaml.load(open("{}.yml".format(SysUtil.get_hostname())))
        camera_conf = global_conf['cameras'].get(self.identifier, default_conf)
        camera_conf = recursive_update(camera_conf, pdict)
        global_conf['cameras'][self.identifier] = camera_conf
        SysUtil.write_global_config(global_conf)

    def mqtt_on_message(self, client, userdata, msg):
        """
        handler for mqtt messages on a per camera basis.

        :param client: mqtt client
        :param userdata: mqtt userdata
        :param msg: message to be decoded
        """

        payload = msg.payload.decode("utf-8").strip()
        self.logger.debug("topic: {} payload: {}".format(msg.topic, payload))
        if msg.topic == "camera/{}/config".format(self.identifier):
            data = json.loads(payload)
            uploaddict = {}
            if "server_dir" in data.keys():
                uploaddict["server_dir"] = data.pop('server_dir')
            if "username" in data.keys():
                uploaddict["username"] = data.pop('username')
            if "password" in data.keys():
                uploaddict["password"] = data.pop('password')
            if "server" in data.keys():
                uploaddict["host"] = data.pop('server')

            if "starttime" in data.keys():
                try:
                    parsed = parser.parse(data["starttime"],
                                          parserinfo=TwentyFourHourTimeParserInfo())
                    self.begin_capture = parsed.time()
                except:
                    pass

            if "stoptime" in data.keys():
                try:
                    parsed = parser.parse(data["stoptime"],
                                          parserinfo=TwentyFourHourTimeParserInfo())
                    self.end_capture = parsed.time()
                except:
                    pass
            if "timestamped" in data.keys():
                data['capture_timelapse'] = data.pop('timestamped')

            self.config = recursive_update(self.config, data)
            for k, v in data.items():
                if hasattr(self, k) and not callable(getattr(self, k)):
                    setattr(self, k, v)

            data['upload'] = uploaddict
            self.set_config(data)

        if msg.topic == "camera/{}/capture".format(self.identifier):
            if payload == "CAPTURE_NOW":
                self.capture_image(self.timestamped_imagename)

    def mqtt_on_connect(self, client, *args):
        self.mqtt.subscribe("camera/{}/config".format(self.identifier), qos=1)
        self.mqtt.subscribe("camera/{}/operation".format(self.identifier), qos=1)

    def setupmqtt(self):
        client_id = str(crc32(bytes(self.identifier, 'utf8')))
        self.mqtt = client.Client(client_id=client_id,
                                  clean_session=True,
                                  protocol=client.MQTTv311,
                                  transport="tcp")

        self.mqtt.on_message = self.mqtt_on_message
        self.mqtt.on_connect = self.mqtt_on_connect
        try:
            with open("mqttpassword") as f:
                self.mqtt.username_pw_set(username=self.identifier,
                                          password=f.read().strip())
        except FileNotFoundError:
            auth = SSHManager().sign_message_PSS(datetime.datetime.now().replace(tzinfo=timezone).isoformat())
            if not auth:
                raise ValueError
            self.mqtt.username_pw_set(username=SysUtil.get_machineid(),
                                      password=auth)
        except:
            self.mqtt.username_pw_set(username=self.identifier,
                                      password="INVALIDPASSWORD")
        self.mqtt.connect_async("10.8.0.1", port=1883)
        self.mqtt.loop_start()

    def updatemqtt(self, msg: bytes):
        # update mqtt
        msg = self.mqtt.publish(payload=msg,
                                topic="camera/{}/capture".format(self.identifier),
                                qos=1)
        time.sleep(0.5)
        if not msg.is_published():
            self.mqtt.loop_stop()
            self.mqtt.loop_start()

    def capture_image(self, filename: str = None) -> numpy.array:
        """
        Camera capture method.
        override this method when creating a new type of camera.

        Behavior:
            - if filename is a string, write images to disk as filename.ext, and return the names of the images written sucessfully.
            - if filename is None, it will set the instance attribute `_image` to a numpy array of the image and return that.

        :param filename: image filename without extension
        :return: :func:`numpy.array` if filename not specified, otherwise list of files.
        :rtype: numpy.array or list(str)
        """
        return self._image

    def capture(self, filename: str = None) -> numpy.array:
        """
        capture method, only extends functionality of :func:`Camera.capture` so that testing with  can happen

        Camera.capture = Camera.capture_monkey
        For extending the Camera class override the Camera.capture_image method, not this one.

        :param filename: image filename without extension
        :return: :func:`numpy.array` if filename not specified, otherwise list of files.
        :rtype: numpy.array
        """

        if filename:
            dirname = os.path.dirname(filename)
            os.makedirs(dirname, exist_ok=True)
        return self.capture_image(filename=filename)

    def capture_monkey(self, filename: str = None) -> numpy.array:
        """
        Simulates things going horribly wrong with the capture.
        Will sometimes return None, an empty list or an invalid filename.
        Sometimes will raise a generic Exception.
        The rest of the time it will capture a valid image.

        :param filename: image filename without extension
        :return: :func:`numpy.array` if filename not specified, otherwise list of files.
        :rtype: numpy.array
        """
        self.logger.warning("Capturing with a naughty monkey.")
        import random
        s = random.uniform(0, 100)
        if s < 10:
            # return nothing
            return None
        elif 10 <= s <= 20:
            # return empty list
            return []
        elif 20 <= s <= 30:
            # return an invalid list of no files
            return ["Ooh ooh, ahh ahhh!"]
        elif 30 <= s <= 40:
            # raise an uncaught exception
            raise Exception("BANANAS")
        elif 40 <= s <= 50:
            # return some random bytes
            return bytes(b'4')
        elif 50 <= s <= 60:
            # return a string
            return "Feed me!"
        else:
            return self.capture_image(filename=filename)

    @property
    def exif(self) -> dict:
        """
        Gets the current exif data, sets the exif datetime field to now.

        :return: dictionary of exif fields and their values.
        :rtype: dict
        """
        self._exif["Exif.Photo.DateTimeOriginal"] = datetime.datetime.now()
        return self._exif

    @property
    def image(self) -> numpy.array:
        """
        Gets the current image (last image taken and stored) as a numpy.array.

        :return: numpy array of the currently stored image.
        :rtype: numpy.array
        """
        return self._image

    @staticmethod
    def timestamp(tn: datetime.datetime) -> str:
        """
        Creates a properly formatted timestamp from a datetime object.

        :param tn: datetime to format to timestream timestamp string
        :return: formatted timestamp.
        """
        return tn.strftime('%Y_%m_%d_%H_%M_%S')

    @staticmethod
    def time2seconds(t: datetime.datetime) -> int:
        """
        Converts a datetime to an integer of seconds since epoch

        :return: integer of seconds since 1970-01-01
        :rtype: int
        """
        try:
            return int(t.timestamp())
        except:
            # the 'timestamp()' method is only implemented in python3.3`
            # this is an old compatibility thing
            return int(t.hour * 60 * 60 + t.minute * 60 + t.second)

    @property
    def timestamped_imagename(self) -> str:
        """
        Builds a timestamped image basename without extension from :func:`Camera.current_capture_time`

        :return: image basename
        :rtype: str
        """
        return '{camera_name}_{timestamp}'.format(camera_name=self.name,
                                                  timestamp=Camera.timestamp(self.current_capture_time))

    @property
    def time_to_capture(self) -> bool:
        """
        Filters out times for capture.

        returns True by default.

        returns False if the conditions where the camera should capture are NOT met.

        :return: whether or not it is time to capture
        :rtype: bool
        """
        current_naive_time = self.current_capture_time.time()

        if self.begin_capture < self.end_capture:
            # where the start capture time is less than the end capture time
            if not self.begin_capture <= current_naive_time <= self.end_capture:
                return False
        else:
            # where the start capture time is greater than the end capture time
            # i.e. capturing across midnight.
            if self.end_capture <= current_naive_time <= self.begin_capture:
                return False

        # capture interval
        if not (self.time2seconds(self.current_capture_time) % self.interval < Camera.accuracy):
            return False
        return True

    @property
    def time_to_report(self) -> bool:
        """
        Filters out times for reporting.

        returns True by default.

        returns False if the conditions where the camera should capture are NOT met.

        :return: whether or not it is time to capture
        :rtype: bool
        """
        # capture interval
        if not (self.time2seconds(self.current_capture_time) % 30 < 1):
            return False
        return True

    def get_exif_fields(self) -> dict:
        """
        Get default fields for exif dict, this should be overriden and super-ed if you want to add custom exif tags.

        :return: exif fields
        :rtype: dict
        """
        exif = dict()
        exif['Exif.Image.Make'] = "Make"
        exif['Exif.Image.Model'] = "Model"
        exif['Exif.Image.CameraSerialNumber'] = self.identifier
        return exif

    def encode_write_np_array(self, np_image_array: numpy.array, fn: str) -> list:
        """
        takes a RGB numpy image array like the ones from cv2 and writes it to disk as a tif and jpg
        converts from rgb to bgr for cv2 so that the images save correctly
        also tries to add exif data to the images

        :param numpy.array np_image_array: 3 dimensional image array, x,y,rgb
        :param str fn: filename
        :return: files successfully written.
        :rtype: list(str)
        """
        # output types must be valid!
        fnp = os.path.splitext(fn)[0]
        successes = list()
        for ext in Camera.output_types:
            fn = "{}.{}".format(fnp, ext)
            s = cv2.imwrite(fn, np_image_array)
            if s:
                successes.append(fn)
                try:
                    # set exif data
                    import pyexiv2
                    meta = pyexiv2.ImageMetadata(fn)
                    meta.read()
                    for k, v in self.exif.items():
                        try:
                            meta[k] = v
                        except:
                            pass
                    meta.write()
                except Exception as e:
                    self.logger.debug("Couldnt write the appropriate metadata: {}".format(str(e)))
        return successes

    @staticmethod
    def _write_raw_bytes(image_bytesio: BytesIO, fn: str) -> list:
        """
        Writes a BytesIO object to disk.

        :param image_bytesio: bytesio of an image.
        :param fn:
        :return: file name
        """
        with open(fn, 'wb') as f:
            f.write(image_bytesio.read())
            # no exif data when writing the purest bytes :-P
        return fn

    def stop(self):
        """
        Stops the capture thread, if self is an instance of :class:`threading.Thread`.
        """
        self.stopper.set()

    def focus(self):
        """
        AutoFocus trigger method.
        Unimplemented.
        """
        pass

    def communicate_with_updater(self):
        """
        Inter-thread communication method.
        Communicates with this objects :class:`libs.Updater.Updater` by keeping a reference to its member
        'communication_queue' and appending this objects current state to the queue.
        """

        try:
            data = dict(
                name=self.name,
                identifier=self.identifier,
                failed=self.failed,
                last_capture=self.current_capture_time.isoformat())
            # append our data dict to the communication_queue deque.
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("Inter-thread communication error: {}".format(str(e)))

    def run(self):
        """
        Main method. continuously captures and stores images.
        """
        while True and not self.stopper.is_set():
            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.__class__._thread is not None:
                self.logger.critical("Camera live view thread is not closed, camera lock cannot be acquired.")
                continue
            last_captured_b = b'asdhjkasdfhjklasdfhjklasdf'
            if self.time_to_capture:
                try:
                    with tempfile.TemporaryDirectory(prefix=self.name) as spool:
                        self.spool_directory = spool
                        start_capture_time = time.time()
                        raw_image = self.timestamped_imagename
                        files = []
                        if self.config.get("capture", True):
                            self.logger.info("Capturing for {}".format(self.identifier))
                            telemetry = dict()
                            files = self.capture(filename=os.path.join(spool, raw_image))
                            # capture. if capture didnt happen dont continue with the rest.
                            if len(files) == 0:
                                self.failed.append(self.current_capture_time)
                                continue

                            telemetry["timing_capture_s"] = float(time.time() - start_capture_time)

                            st = time.time()
                            resize_t = 0.0
                            if self.config.get("resize_last", False):
                                self._image = cv2.resize(self._image, (Camera.default_width, Camera.default_height),
                                                         interpolation=cv2.INTER_NEAREST)
                                resize_t = time.time() - st

                            cv2.putText(self._image,
                                        self.timestamped_imagename,
                                        org=(20, self._image.shape[0] - 20),
                                        fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                                        fontScale=1,
                                        color=(0, 0, 255),
                                        thickness=2,
                                        lineType=cv2.LINE_AA)

                            cv2.imwrite(os.path.join("/dev/shm", self.identifier + ".jpg"), self._image)
                            shutil.copy(os.path.join("/dev/shm", self.identifier + ".jpg"),
                                        os.path.join(self.upload_directory, "last_image.jpg"))
                            telemetry["timing_resize_s"] = float(resize_t)
                            self.logger.info("Resize {0:.3f}s, total: {0:.3f}s".format(resize_t, time.time() - st))

                            # copying/renaming for files
                            oldfiles = files[:]
                            files = []

                            for fn in oldfiles:
                                if type(fn) is list:
                                    files.extend(fn)
                                else:
                                    files.append(fn)
                        try:
                            telemetry["num_files_created"] = len(files)
                        except:
                            pass
                        for fn in files:
                            # move files to the upload directory
                            try:
                                if self.config.get("capture_timelapse", False):
                                    shutil.move(fn, self.upload_directory)
                                    self.logger.info("Captured & stored for upload - {}".format(os.path.basename(fn)))
                            except Exception as e:
                                self.logger.error("Couldn't move for timestamped: {}".format(str(e)))

                            # remove the spooled files that remain
                            try:
                                if os.path.isfile(fn):
                                    self.logger.info("File remaining in spool directory, removing: {}".format(fn))
                                    os.remove(fn)
                            except Exception as e:
                                self.logger.error("Couldn't remove spooled when it still exists: {}".format(str(e)))
                        # log total capture time
                        total_capture_time = time.time() - start_capture_time
                        self.logger.info("Total capture time: {0:.2f}s".format(total_capture_time))
                        telemetry["timing_total_s"] = float(total_capture_time)
                        # communicate our success with the updater
                        try:
                            telegraf_client = telegraf.TelegrafClient(host="localhost", port=8092)
                            telegraf_client.metric("camera", telemetry, tags={"camera_name": self.name})
                            self.logger.debug("Communicated sesor data to telegraf")
                        except Exception as exc:
                            self.logger.error("Couldnt communicate with telegraf client. {}".format(str(exc)))

                        last_captured_b = bytes(self.current_capture_time.replace(tzinfo=timezone).isoformat(), 'utf-8')
                        # self.communicate_with_updater()
                        # sleep for a little bit so we dont try and capture again so soon.
                        time.sleep(Camera.accuracy * 2)
                except Exception as e:
                    self.logger.critical("Image Capture error - {}".format(str(e)))
                    self.logger.critical(traceback.format_exc())
            if self.time_to_report:
                try:
                    self.updatemqtt(last_captured_b)
                except:
                    pass
            time.sleep(1)


class IPCamera(Camera):
    """
    IPCamera, unfinished and untested.

    TODO: needs work to support both yml config and normal configs
    """

    def __init__(self, identifier=None, ip=None, config=None, queue=None, **kwargs):
        if not config:
            config = dict()
        self.config = config.copy()
        self.communication_queue = queue or deque(tuple(), 256)
        self.return_parser = config.get("return_parser", "plaintext")
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier

        self.camera_name = config.get("camera_name", identifier)
        self.interval = int(config.get("interval", 300))

        self.upload_directory = config.get("upload_dir", os.path.join(os.getcwd(), identifier))
        self.begin_capture = datetime.time(0, 0)
        self.end_capture = datetime.time(23, 59)

        start_time_string = str(self.config.get('starttime', "00:00"))
        start_time_string = start_time_string.replace(":", "")
        end_time_string = str(self.config.get('stoptime', "23:59"))
        end_time_string = end_time_string.replace(":", "")
        try:
            start_time_string = start_time_string[:4]
            assert end_time_string.isdigit(), "Non numerical start time, {}".format(str(end_time_string))
            self.begin_capture = datetime.datetime.strptime(start_time_string, "%H%M").time()
        except Exception as e:
            self.logger.error("Time conversion error starttime - {}".format(str(e)))
        try:
            # cut string to max of 4.
            end_time_string = end_time_string[:4]
            assert end_time_string.isdigit(), "Non numerical end time, {}".format(str(end_time_string))
            self.end_capture = datetime.datetime.strptime(end_time_string, "%H%M").time()
        except Exception as e:
            self.logger.error("Time conversion error stoptime - {}".format(str(e)))

        self.failed = list()
        self._image = numpy.empty((Camera.default_width, Camera.default_height, 3), numpy.uint8)

        try:
            if not os.path.exists(self.upload_directory):
                self.logger.info("Creating upload dir {}".format(self.upload_directory))
                os.makedirs(self.upload_directory)
        except Exception as e:
            self.logger.error("Creating directories {}".format(str(e)))

        self._exif = self.get_exif_fields()

        self.current_capture_time = datetime.datetime.now()

        self._image = None

        self._notified = []

        format_str = config.get("format_url", "http://{HTTP_login}@{ip}{command}")
        self.auth_type = config.get("auth_type", "basic")
        self.auth_object = None
        if format_str.startswith("http://{HTTP_login}@"):
            format_str = format_str.replace("{HTTP_login}@", "")
            self.auth_object = HTTPBasicAuth(config.get("username", "admin"),
                                             config.get("password", "admin"))
            self.auth_object_digest = HTTPDigestAuth(config.get("username", "admin"),
                                                     config.get("password", "admin"))
            self.auth_object = self.auth_object_digest if self.auth_type == "digest" else self.auth_object

        self._HTTP_login = config.get("HTTP_login", "{user}:{password}").format(
            user=config.get("username", "admin"),
            password=config.get("password", "admin"))

        self._url = format_str.format(
            ip=ip or config.get("ip", "192.168.1.7"),
            HTTP_login=self._HTTP_login,
            command="{command}")

        self._image_size_list = config.get("image_size_list", [[1920, 1080], [1280, 720], [640, 480]])
        self._image_size = config.get("image_size", self._image_size_list[0])
        image_quality = config.get("image_quality", 100)
        self._image_quality = image_quality
        # no autofocus modes by default.
        self._autofocus_modes = config.get("autofocus_modes", [])

        self._hfov_list = config.get("horizontal_fov_list",
                                     [71.664, 58.269, 47.670, 40.981, 33.177, 25.246, 18.126, 12.782, 9.217, 7.050,
                                      5.82])
        self._vfov_list = config.get("vertical_fov_list",
                                     [39.469, 33.601, 26.508, 22.227, 16.750, 13.002, 10.324, 7.7136, 4.787, 3.729,
                                      2.448])
        self._hfov = self._vfov = None
        self._zoom_list = config.get("zoom_list", [50, 150, 250, 350, 450, 550, 650, 750, 850, 950, 1000])

        self._focus_range = config.get("focus_range", [1, 99999])

        # set commands from the rest of the config.
        self.command_urls = config.get('urls', {})
        self.return_keys = config.get("keys", {})

        self.image_quality = self.image_quality

        super(IPCamera, self).__init__(identifier, config=config, **kwargs)

        self.logger.info(self.status)

    def _make_request(self, command_string, *args, **kwargs):
        """
        Makes a generic request formatting the command string and applying the authentication.

        :param command_string: command string like read stream raw
        :type command_string: str
        :param args:
        :param kwargs:
        :return:
        """
        url = self._url.format(*args, command=command_string, **kwargs)
        if "&" in url and "?" not in url:
            url = url.replace("&", "?", 1)
        response = None
        try:
            response = requests.get(url, auth=self.auth_object)
            if response.status_code == 401:
                self.logger.debug("Auth is not basic, trying digest")
                response = requests.get(url, auth=self.auth_object_digest)
        except Exception as e:
            self.logger.error("Some exception got raised {}".format(str(e)))
            return
        if response.status_code not in [200, 204]:
            self.logger.error(
                "[{}] - {}\n{}".format(str(response.status_code), str(response.reason), str(response.url)))
            return
        return response

    def _read_stream(self, command_string, *args, **kwargs):
        """
        opens a url with the current HTTP_login string
        :type command_string: str
        :param command_string: url to go to with parameters
        :return: string of data returned from the camera
        """
        response = self._make_request(command_string, *args, **kwargs)
        if response is None:
            return
        return response.text

    def _read_stream_raw(self, command_string, *args, **kwargs):
        """
        opens a url with the current HTTP_login string

        :param command_string: url to go to with parameters
        :type command_string: str
        :return: string of data returned from the camera
        """
        response = self._make_request(command_string, *args, **kwargs)
        if response is None:
            return
        return response.content

    def _get_cmd(self, cmd):
        cmd_str = self.command_urls.get(cmd, None)
        if not cmd_str and cmd_str not in self._notified:
            print("No command available for \"{}\"".format(cmd))
            self._notified.append(cmd_str)
            return None, None
        keys = self.return_keys.get(cmd, [])
        if type(keys) not in (list, tuple):
            keys = [keys]
        return cmd_str, keys

    @staticmethod
    def get_value_from_xml(message_xml, *args):
        """
        gets float, int or string values from a xml string where the key is the tag of the first element with value as
        text.

        :param message_xml: the xml to searach in.
        :param args: list of keys to find values for.
        :rtype: dict
        :return: dict of arg: value pairs requested
        """
        return_values = dict()
        if not len(args):
            return return_values
        if not len(message_xml):
            return return_values
        # apparently, there is an issue parsing when the ptz returns INVALID XML (WTF?)
        # these seem to be the tags that get mutilated.
        illegal = ['\n', '\t', '\r',
                   "<CPStatusMsg>", "</CPStatusMsg>", "<Text>",
                   "</Text>", "<Type>Info</Type>", "<Type>Info",
                   "Info</Type>", "</Type>", "<Type>"]
        for ill in illegal:
            message_xml = message_xml.replace(ill, "")

        root_element = ElementTree.Element("invalidation_tag")
        try:
            root_element = ElementTree.fromstring(message_xml)

        except Exception as e:
            print(str(e))
            print("Couldnt parse XML!!!")
            print(message_xml)

        return_values = dict
        for key in args:
            target_ele = root_element.find(key)
            if target_ele is None:
                continue

            value = target_ele.text.replace(' ', '')
            if value is None:
                continue

            types = [float, int, str]
            for t in types:
                try:
                    return_values[key] = t(value)
                    break
                except ValueError:
                    pass
            else:
                print("Couldnt cast an xml element text attribute to str. What are you feeding the xml parser?")

        return return_values

    @staticmethod
    def get_value_from_plaintext(message, *args):
        """
        gets float, int or string values from a xml string where the key is the tag of the first element with value as
        text.

        :param message:
        :param args: list of keys to find values for.
        :rtype: dict
        :return: dict of arg: value pairs requested
        """
        return_values = dict()
        if not len(args):
            return return_values
        if not len(message):
            return return_values
        for line in message.split("\n"):
            line = line.replace("= ", "=").replace(" =", "=").strip()
            name, value = line.partition("=")[::2]
            name, value = name.strip(), value.strip()
            types = [float, int, str]
            if name in args:
                for t in types:
                    try:
                        v = t(value)
                        if str(v).lower() in ['yes', 'no', 'true', 'false', 'on', 'off']:
                            v = str(v).lower() in ['yes', 'true', 'on']
                        return_values[name] = v
                        break
                    except ValueError:
                        pass
                else:
                    print("Couldnt cast an plaintext element text attribute to str. What are you feeding the parser?")
        return return_values

    def get_value_from_stream(self, stream, *keys):
        """
        Gets a value from some text data (xml or plaintext = separated values)
        returns a dict of "key":value pairs.

        :param stream: text data to search for values
        :type stream: str
        :param keys:
        :type keys: list
        :return: dict of values
        :rtype: dict
        """
        if self.return_parser == 'plaintext':
            return self.get_value_from_plaintext(stream, *keys)
        elif self.return_parser == 'xml':
            return self.get_value_from_xml(stream, *keys)
        else:
            return dict()

    def capture_image(self, filename=None) -> numpy.array:
        """
        Captures an image with the IP camera, uses requests.get to acqire the image.

        :param filename: filename without extension to capture to.
        :return: list of filenames (of captured images) if filename was specified, otherwise a numpy array of the image.
        :rtype: numpy.array or list
        """
        st = time.time()
        cmd, keys = self._get_cmd("get_image")
        if "{width}" in cmd and "{height}" in cmd:
            cmd = cmd.format(width=self._image_size[0], height=self.image_size[1])
        if not cmd:
            self.logger.error("No capture command, this is wrong...")
            return self._image

        url = self._url.format(command=cmd)
        for x in range(10):
            try:
                # fast method
                a = self._read_stream_raw(cmd)
                b = numpy.fromstring(a, numpy.uint8)
                self._image = cv2.imdecode(b, cv2.IMREAD_COLOR)
                if filename:
                    rfiles = self.encode_write_np_array(self._image, filename)
                    self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                    return rfiles
                else:
                    self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                    break
            except Exception as e:
                self.logger.error("Capture from network camera failed {}".format(str(e)))
            time.sleep(0.2)
        else:
            self.logger.error("All capture attempts (10) for network camera failed.")
        return self._image

    # def set_fov_from_zoom(self):
    #     self._hfov = numpy.interp(self._zoom_position, self.zoom_list, self.hfov_list)
    #     self._vfov = numpy.interp(self._zoom_position, self.zoom_list, self.vfov_list)

    @property
    def image_quality(self) -> float:
        """
        Image quality as a percentage.

        :getter: cached.
        :setter: to camera.
        :rtype: float
        """
        return self._image_quality

    @image_quality.setter
    def image_quality(self, value: float):
        assert (1 <= value <= 100)
        cmd, keys = self._get_cmd("get_image_quality")
        if cmd:
            self._read_stream(cmd.format(value))

    @property
    def image_size(self) -> list:
        """
        Image resolution in pixels, tuple of (width, height)

        :getter: from camera.
        :setter: to camera.
        :rtype: tuple
        """
        cmd, keys = self._get_cmd("get_image_size")
        if cmd:
            stream = self._read_stream(cmd)
            output = self.get_value_from_stream(stream, keys)
            width, height = self._image_size
            for k, v in output.items():
                if "width" in k:
                    width = v
                if "height" in k:
                    height = v
            self._image_size = [width, height]
        return self._image_size

    @image_size.setter
    def image_size(self, value):
        assert type(value) in (list, tuple), "image size is not a list or tuple!"
        assert len(value) == 2, "image size doesnt have 2 elements width,height are required"
        value = list(value)
        assert value in self._image_size_list, "image size not in available image sizes"
        cmd, keys = self._get_cmd("set_image_size")
        if cmd:
            self._read_stream(cmd.format(width=value[0], height=value[1]))
            self._image_size = value

    @property
    def focus_mode(self) -> str:
        """
        TODO: this is broken, returns the dict of key: value not value

        Focus Mode

        When setting, the mode provided must be in 'focus_modes'

        :getter: from camera.
        :setter: to camera.
        :rtype: list
        """
        cmd, keys = self._get_cmd("get_focus_mode")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        return self.get_value_from_stream(stream_output, keys)['mode']

    @focus_mode.setter
    def focus_mode(self, mode: str):
        assert (self._autofocus_modes is not None)
        if mode.upper() not in self._autofocus_modes:
            print("Focus mode not in list of supported focus modes. YMMV.")
        cmd, keys = self._get_cmd("set_focus_mode")
        if cmd:
            self._read_stream(cmd.format(mode=mode))

    @property
    def focus_position(self):
        """
        Focal position as an absolute value.

        :getter: from camera.
        :setter: to camera.
        :rtype: float
        """
        cmd, keys = self._get_cmd("get_focus")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        result = self.get_value_from_stream(stream_output, keys)
        return next(iter(result), float(99999))

    @focus_position.setter
    def focus_position(self, absolute_position):
        self.logger.debug("Setting focus position to {}".format(absolute_position))
        cmd, key = self._get_cmd("set_focus")
        if not cmd:
            assert (self._focus_range is not None and absolute_position is not None)
            absolute_position = min(self._focus_range[1], max(self._focus_range[0], absolute_position))
            assert (self._focus_range[0] <= absolute_position <= self._focus_range[1])
            self._read_stream(cmd.format(focus=absolute_position))

    def focus(self):
        """
        focuses the camera by cycling it through its autofocus modes.
        """
        self.logger.debug("Focusing...")
        cmd, key = self._get_cmd("set_autofocus_mode")
        if not cmd or len(self._autofocus_modes) < 1:
            return
        for mode in self._autofocus_modes:
            self.focus_mode = mode
            time.sleep(2)
        self._read_stream(cmd.format(mode=self._autofocus_modes[0]))
        time.sleep(2)
        self.logger.debug("Focus complete.")

    @property
    def focus_range(self):
        """
        Information about the focus of the camera

        :return: focus type, focus max, focus min
        :rtype: list [str, float, float]
        """
        cmd, keys = self._get_cmd("get_focus_range")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        values = self.get_value_from_stream(stream_output, keys)
        return values[2:0:-1]

    @property
    def hfov_list(self):
        """
        List of horizontal FoV values according to focus list.

        :getter: cached.
        :setter: cache.
        :rrtype: list(float)
        """
        return self._hfov_list

    @hfov_list.setter
    def hfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        # assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._hfov_list = list(value)

    @property
    def vfov_list(self):
        """
        List of vertical FoV values according to focus list.

        :getter: cached.
        :setter: cache.
        :rrtype: list(float)
        """
        return self._vfov_list

    @vfov_list.setter
    def vfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        # assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._vfov_list = list(value)

    @property
    def hfov(self):
        """
        Horizontal FoV

        :getter: calculated using cached zoom_position, zoom_list and hfov_list.
        :setter: cache.
        :rrtype: list(float)
        """
        # self._hfov = numpy.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        return self._hfov

    @hfov.setter
    def hfov(self, value: float):
        self._hfov = value

    @property
    def vfov(self):
        """
        Vertical FoV

        :getter: calculated using cached zoom_position, zoom_list and vfov_list.
        :setter: cache.
        :rrtype: list(float)
        """
        # self._vfov = numpy.interp(self._zoom_position, self.zoom_list, self.vfov_list)
        return self._vfov

    @vfov.setter
    def vfov(self, value: float):
        self._vfov = value

    @property
    def status(self) -> str:
        """
        Helper property for a string of the current zoom/focus status.

        :return: informative string of zoom_pos zoom_range focus_pos focus_range
        :rtype: str
        """
        # fmt_string = "zoom_pos:\t{}\nzoom_range:\t{}"
        fmt_string = "".join(("\nfocus_pos:\t{}\nfocus_range:\t{}"))
        return fmt_string.format(self.focus_position, self.focus_range)


class GPCamera(Camera):
    """
    Camera class
    other cameras inherit from this class.
    identifier and usb_address are NOT OPTIONAL
    """

    def __init__(self, identifier: str, usb_address: tuple = None, lock=Lock(), **kwargs):
        """
        Providing a usb address and no identifier or an identifier but no usb address will cause
        
        :param identifier: 
        :param lock: 
        :param usb_address: 
        :param kwargs: 
        """

        self.lock = lock
        self.usb_address = [None, None]
        self._serialnumber = identifier
        self.identifier = identifier
        if type(usb_address) is tuple and len(usb_address) is 2:
            self.usb_address = usb_address
        if self.usb_address[0] is None:
            with self.lock:
                serialnumber = None
                camera = None
                if self.identifier is not None:
                    for cam in gp.list_cameras():
                        try:
                            serialnumber = cam.status.serialnumber
                            if serialnumber in self.identifier:
                                camera = cam
                                break
                        except:
                            pass
                    else:
                        raise IOError("Camera not available or connected")
                else:
                    for cam in gp.list_cameras():
                        try:
                            serialnumber = str(cam.status.serialnumber)
                            self.identifier = SysUtil.default_identifier(prefix=serialnumber)
                            camera = cam
                            break
                        except:
                            pass
                    else:
                        raise IOError("No cameras available")
                self.usb_address = camera._usb_address
                self._serialnumber = serialnumber
                camera.release()

        super().__init__(self.identifier, **kwargs)

        self.logger.info("Camera detected at usb port {}:{}".format(*self.usb_address))
        try:
            self.exposure_length = self.config.getint("camera", "exposure")
        except:
            pass

    def get_exif_fields(self):
        """
        This is meant to get the exif fields for the image if we want to manually save them.
        This is incomplete.

        :return: dictionary of exif fields.
        :rtype: dict
        """
        exif = super(GPCamera, self).get_exif_fields()
        try:
            camera = self._get_camera()
            exif['Exif.Image.Make'] = getattr(camera.status, 'manufacturer', 'Make')
            exif['Exif.Image.Model'] = getattr(camera.status, 'cameramodel', 'Model')
            exif['Exif.Image.BodySerialNumber'] = self.eos_serial_number
            exif['Exif.Image.CameraSerialNumber'] = self.serial_number
            try:
                exif['Exif.Photo.ISOSpeed'] = self['iso'].value
            except:
                pass
            try:
                exif['Exif.Photo.Aperture'] = self['aperture'].value
            except:
                pass
        except Exception as e:
            self.logger.warning("Couldnt get full exif data. {}".format(str(e)))
        return exif

    def _get_camera(self):
        with self.lock:
            try:
                camera = gp.Camera(bus=self.usb_address[0], device=self.usb_address[1])
                if self._serialnumber == camera.status.serialnumber:
                    self.logger.debug("Camera matched for {}:{}".format(*self.usb_address))
                    return camera
            except Exception as e:
                self.logger.warning(
                    "Camera wasnt at the correct usb address or usb address wasnt specified: {}".format(str(e)))

            for camera in gp.list_cameras():
                try:
                    if camera.status.serialnumber == self._serialnumber:
                        return camera
                except Exception as e:
                    self.logger.warning("Couldnt acquire lock for camera: {}".format(str(e)))
            else:
                raise FileNotFoundError("Camera cannot be found")

    def capture_image(self, filename=None):
        """
        Gapture method for DSLRs.
        Some contention exists around this method, as its definitely not the easiest thing to have operate robustly.
        :func:`GPCamera._cffi_capture` is how it _should_ be done, however that method is unreliable and causes many
        crashes when in real world timelapse situations.
        This method calls gphoto2 directly, which makes us dependent on gphoto2 (not just libgphoto2 and gphoto2-cffi),
        and there is probably some issue with calling gphoto2 at the same time like 5 times, maybe dont push it.

        :param filename: filename without extension to capture to.
        :return: list of filenames (of captured images) if filename was specified, otherwise a numpy array of the image.
        :rtype: numpy.array or list
        """
        import subprocess
        import glob

        # the %C filename parameter given to gphoto2 will automatically expand the number of image types that the
        # camera is set to capture to.

        # this one shouldnt really be used.
        fn = "{}-temp.%C".format(self.name)
        if filename:
            # if target file path exists
            fn = os.path.join(self.spool_directory, "{}.%C".format(filename))

        cmd = [
            "gphoto2",
            "--port=usb:{bus:03d},{dev:03d}".format(bus=self.usb_address[0], dev=self.usb_address[1]),
            "--set-config=capturetarget=0",  # capture to sdram
            "--force-overwrite",  # if the target image exists. If this isnt present gphoto2 will lock up asking
            "--capture-image-and-download",  # must capture & download in the same call to use sdram target.
            '--filename={}'.format(fn)
        ]
        self.logger.debug("Capture start: {}".format(fn))
        for tries in range(6):
            self.logger.debug("CMD: {}".format(" ".join(cmd)))
            try:
                output = subprocess.check_output(cmd, stderr=subprocess.STDOUT, universal_newlines=True)

                if "error" in output.lower():
                    raise subprocess.CalledProcessError("non-zero exit status", cmd=cmd, output=output)
                else:
                    # log success of capture
                    self.logger.info("GPCamera capture success: {}".format(fn))
                    for line in output.splitlines():
                        self.logger.debug("GPHOTO2: {}".format(line))
                    # glob up captured images
                    filenames = glob.glob(fn.replace("%C", "*"))
                    # if there are no captured images, log the error
                    if not len(filenames):
                        self.logger.error("capture resulted in no files.")
                    else:
                        # try and load an image for the last_image.jpg resized doodadery
                        try:
                            first = filenames[0] if filenames else None
                            self._image = cv2.cvtColor(cv2.imread(first, cv2.IMREAD_COLOR), cv2.COLOR_BGR2RGB)
                        except Exception as e:
                            self.logger.error("Failed to set current image: {}".format(str(e)))

                        if filename:
                            # return the filenames of the spooled images if files were requestsed.
                            return filenames
                        else:
                            # otherwise remove the temporary files that we created in order to fill self._image
                            for fp in filenames:
                                os.remove(fp)
                            # and return self._image
                            return self._image

            except subprocess.CalledProcessError as e:
                self.logger.error("failed {} times".format(tries))
                for line in e.output.splitlines():
                    if not line.strip() == "" and "***" not in line:
                        self.logger.error(line.strip())
        else:
            self.logger.critical("Really bad stuff happened. too many tries capturing.")
            if filename:
                return []
        return None

    def _cffi_capture(self, filename=None):
        """
        old cffi capture. very unreliable.
        Causes a memory leak somewhere that I cant find.

        :param filename: filename without extension to capture to.
        :return: list of filenames (of captured images) if filename was specified, otherwise a numpy array of the image.
        :rtype: numpy.array or list
        """
        st = time.time()
        camera = None
        for x in range(10):
            try:
                camera = self._get_camera()
                successes = list()
                size = 0
                for idx, image in enumerate(list(camera.capture(img_expect_count=2, timeout=20))):
                    with image:
                        try:
                            size += image.size
                            fn = (filename or os.path.splitext(image.filename)[0]) + os.path.splitext(image.filename)[
                                -1]
                            if idx == 0:
                                self._image = cv2.imdecode(numpy.fromstring(image.read(), numpy.uint8),
                                                           cv2.IMREAD_COLOR)
                            image.save(fn)
                            successes.append(fn)
                            try:
                                image.remove()
                            except Exception as e:
                                self.logger.info("Couldnt remove image for some reason (probably already gone)")
                            del image
                            self.logger.debug("Captured and stored: {}".format(fn))
                        except:
                            # cant do anything if failure here.
                            pass

                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                self.logger.debug("Filesize {}".format(size))
                if filename:
                    return successes
                return self._image
            except Exception as e:
                self.logger.error("Error Capturing with DSLR: {}".format(str(e)))
                time.sleep(1)
            finally:
                if camera:
                    camera.release()
        else:
            self.logger.fatal("(10) Tries capturing failed")
            if filename:
                return []
        return None

    def __getitem__(self, item):
        return next(iter(self._config(item)), None)

    @property
    def serial_number(self) -> str:
        """
        returns the current serialnumber for the camera.
        """
        return self._serialnumber

    def focus(self):
        """
        this is meant to trigger the autofocus. currently not in use because it causes some distortion in the images.
        """
        camera = self._get_camera()
        try:
            pass
            # camera._get_config()['actions']['eosremoterelease'].set("Release Full")
            # camera._get_config()['actions']['eosremoterelease'].set("Press 1")
            # camera._get_config()['actions']['eosremoterelease'].set("Release Full")
        except Exception as e:
            print(str(e))

    @property
    def eos_serial_number(self) -> str or None:
        """
        returns the eosserialnumber of supported cameras, otherwise the normal serialnumber
        """
        camera = self._get_camera()
        sn = vars(camera.status).get("eosserialnumber", self.serial_number)
        camera.release()
        return sn

    def _config(self, field: str) -> list:
        """
        searches for a field from the camera config.

        :param field: string to search
        :return: list of matching fields, should mostly be len 1
        """
        fields_found = []
        camera = self._get_camera()
        config = camera._get_config()
        camera.release()
        return list(nested_lookup(field, config))


class USBCamera(Camera):
    """
    USB Camera Class
    """

    @classmethod
    def stream_thread(cls):
        """
        usb camera stream thread.
        TODO: Needs to be aware of multiple cameras.
        """
        print("ThreadStartup ...")
        cam = cv2.VideoCapture()

        # camera setup
        # let camera warm up
        time.sleep(2)
        cam.set(3, 30000)
        cam.set(4, 30000)

        print("Started up!")
        # for foo in camera.capture_continuous(stream, 'jpeg',
        #                                      use_video_port=True):
        while True:
            ret, frame = cam.read()
            frame = cv2.imencode(".jpg", frame)
            cls._frame = frame[1].tostring()
            # store frame

            # if there hasn't been any clients asking for frames in
            # the last 10 seconds stop the thread
            if time.time() - cls._last_access > 10:
                print("ThreadShutdown")
                break
        cls._thread = None

    def __init__(self, identifier: str, sys_number: int, **kwargs):
        """
        USB camera init. must have a sys_number (the 0 from /dev/video0) to capture from

        :param identifier: identifier for the webcamera
        :param sys_number: system device number of device to use
        :param kwargs:
        """

        self.logger = logging.getLogger(identifier)
        # only webcams have a v4l sys_number.
        self.sys_number = int(sys_number)
        self.video_capture = None
        try:
            self.video_capture = cv2.VideoCapture()
        except Exception as e:
            self.logger.fatal("couldnt open video capture device on {}".format(self.sys_number))

        self._assert_capture_device()
        try:
            if not self.video_capture.open(self.sys_number):
                self.logger.fatal("Couldnt open a video capture device on {}".format(self.sys_number))
        except Exception as e:
            self.logger.fatal("Couldnt open a video capture device")
        # 3 -> width 4->height 5->fps just max them out to get the highest resolution.
        self.video_capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 100000)
        self.video_capture.set(cv2.CAP_PROP_FRAME_WIDTH, 100000)
        self.logger.info("Capturing at {w}x{h}".format(w=self.video_capture.get(cv2.CAP_PROP_FRAME_WIDTH),
                                                       h=self.video_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)))

        super(USBCamera, self).__init__(identifier, **kwargs)

    def stop(self):
        """
        releases the video device and stops the camera thread
        """
        try:
            self.video_capture.release()
        except Exception as e:
            self.logger.error("Couldnt release cv2 device {}".format(str(e)))
        self.stopper.set()

    def _assert_capture_device(self):
        """
        ensures the capture device is open and valid.

        :param self:
        """
        try:
            if not self.video_capture:
                self.video_capture = cv2.VideoCapture()

            if not self.video_capture.isOpened():
                if not self.video_capture.open(self.sys_number):
                    raise IOError("VideoCapture().open({}) failed.".format(self.sys_number))
        except Exception as e:
            self.logger.error("Capture device could not be opened {}".format(str(e)))

    def capture_image(self, filename=None):
        """
        captures an image from the usb webcam.
        Writes some limited exif data to the image if it can.

        :param filename: filename to output without excension
        :return: list of image filenames if filename was specified, otherwise a numpy array.
        :rtype: numpy.array or list
        """

        st = time.time()
        for _ in range(50):
            try:
                ret, im = self.video_capture.read()
                if ret:
                    self._image = im
                    break
                time.sleep(0.1)
            except Exception as e:
                self.logger.error("Error webcam capture did not read {}".format(str(e)))
        else:
            return None

        if filename:
            try:
                filenames = self.encode_write_np_array(self._image, filename)
                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                return filenames
            except Exception as e:
                self.logger.error("Could not write image {}".format(str(e)))
        else:
            self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
            return self._image
        return None


class PiCamera(Camera):
    """
    Picamera extension to the Camera abstract class.
    """

    @classmethod
    def stream_thread(cls):
        """
        Streaming thread member.

        uses :func:`picamera.PiCamera.capture_continuous` to stream data from the rpi camera video port.

        :func:`time.sleep` added to rate limit a little bit.

        """
        import picamera
        print("start thread")
        try:
            with picamera.PiCamera() as camera:
                # camera setup
                camera.resolution = (640, 480)
                # camera.hflip = True
                # camera.vflip = True

                # let camera warm up
                camera.start_preview()
                time.sleep(2)

                stream = BytesIO()
                for foo in camera.capture_continuous(stream, 'jpeg',
                                                     use_video_port=True):
                    # store frame
                    stream.seek(0)
                    cls._frame = stream.read()

                    # reset stream for next frame
                    stream.seek(0)
                    stream.truncate()

                    # if there hasn't been any clients asking for frames in
                    # the last 10 seconds stop the thread
                    time.sleep(0.01)
                    if time.time() - cls._last_access > 1:
                        break
        except Exception as e:
            print("Couldnt acquire camera")
        print("Closing Thread")
        cls._thread = None

    def set_camera_settings(self, camera):
        """
        Sets the camera resolution to the max resolution

        if the config provides camera/height or camera/width attempts to set the resolution to that.
        if the config provides camera/isoattempts to set the iso to that.
        if the config provides camera/shutter_speed to set the shutterspeed to that.

        :param picamera.PiCamera camera: picamera camera instance to modify
        """
        try:
            camera.resolution = camera.MAX_RESOLUTION
            if type(self.config) is dict:
                if hasattr(self, "width") and hasattr(self, "height"):
                    camera.resolution = (int(self.width),
                                         int(self.height))

                camera.shutter_speed = getattr(self, "shutter_speed", camera.shutter_speed)
                camera.iso = getattr(self, "iso", camera.iso)
            else:
                if self.config.has_option("camera", "width") and self.config.has_option("camera", "height"):
                    camera.resolution = (self.config.getint("camera", "width"),
                                         self.config.getint("camera", "height"))
                if self.config.has_option("camera", "shutter_speed"):
                    camera.shutter_speed = self.config.getfloat("camera", "shutter_speed")
                if self.config.has_option("camera", "iso"):
                    camera.iso = self.config.getint("camera", "iso")
        except Exception as e:
            self.logger.error("error setting picamera settings: {}".format(str(e)))

    def capture_image(self, filename: str = None) -> numpy.array:
        """
        Captures image using the Raspberry Pi Camera Module, at either max resolution, or resolution
        specified in the config file.

        Writes images disk using :func:`encode_write_np_array`, so it should write out to all supported image formats
        automatically.

        :param filename: image filename without extension
        :return: :func:`numpy.array` if filename not specified, otherwise list of files.
        :rtype: numpy.array
        """
        st = time.time()
        try:
            with picamera.PiCamera() as camera:
                with picamera.array.PiRGBArray(camera) as output:
                    time.sleep(2)  # Camera warm-up time
                    self.set_camera_settings(camera)
                    time.sleep(0.2)
                    self._image = numpy.empty((camera.resolution[1], camera.resolution[0], 3), dtype=numpy.uint8)
                    camera.capture(output, 'rgb')
                    self._image = output.array
                    self._image = cv2.cvtColor(self._image, cv2.COLOR_BGR2RGB)
            if filename:
                filenames = self.encode_write_np_array(self._image, filename)
                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                return filenames
            else:
                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
        except Exception as e:
            self.logger.critical("EPIC FAIL, trying other method. {}".format(str(e)))
        return self._image


class IVPortCamera(PiCamera):
    """
    IVPort class for multiple capture.
    the 4 tags on the IVport are setout below.
    """
    current_camera_index = 0

    # these are for the video streaming
    select = 7

    enable_pins = {
        "A": [11, 12],
        "B": [15, 16],
        "C": [21, 22],
        "D": [23, 24]
    }

    TRUTH_TABLE = [
        [False, False, True],
        [True, False, True],
        [False, True, False],
        [True, True, False]
    ]
    gpio_groups = ("B",)

    def __init__(self,
                 identifier: str,
                 gpio_group: tuple = ("B",),
                 camera_number: int = None, **kwargs):
        """
        special __init__ for the IVport to set the gpio enumeration
        This controls which gpio are on or off to select the camera and whcih camera group has been soldered on the
        ivport. Multiple camera groups can be specified, and they will be enumerated in alphabetical order.

        :param identifier: string identifier for the camera
        :type identifier: str
        :param queue: communication queue for the camera to communicate with the updater
        :type queue: deque
        :param kwargs:
        """
        self.__class__.gpio_groups = sorted(gpio_group)

        if camera_number is None:
            super(IVPortCamera, self).__init__(identifier, **kwargs)
        else:
            self.__class__.current_camera_index = camera_number
            IVPortCamera.switch(idx=self.__class__.current_camera_index)

    @classmethod
    def switch(cls, idx: int = None):
        """
        switches the IVPort to a new camera
        with no index, switches to the next camera, looping around from the beginning

        :param idx: index to switch the camera to (optional)
        :type idx: int
        """
        time.sleep(1)
        # import RPi.GPIO as GPIO
        cls.current_camera_index += 1
        if idx is not None:
            cls.current_camera_index = idx

        cls.current_camera_index %= (len(IVPortCamera.TRUTH_TABLE) * len(cls.gpio_groups))
        # GPIO.setwarnings(False)
        # GPIO.setmode(GPIO.BOARD)
        # GPIO.setup(IVPortCamera.select, GPIO.OUT)

        # current groups determined by the camera index / number of cameras per board (truth table len)
        current_group = cls.gpio_groups[int(cls.current_camera_index / len(IVPortCamera.TRUTH_TABLE))]
        current_pins = cls.enable_pins[current_group]
        print("Switching to camera {}: {}".format(current_group, cls.current_camera_index))

        # GPIO.setup(current_pins[0], GPIO.OUT)
        # GPIO.setup(current_pins[1], GPIO.OUT)

        # per camera index, current camera index mod the number of cameras per board
        truth_table_idx = cls.current_camera_index % len(IVPortCamera.TRUTH_TABLE)

        pin_values = [
            IVPortCamera.TRUTH_TABLE[truth_table_idx][0],
            IVPortCamera.TRUTH_TABLE[truth_table_idx][1],
            IVPortCamera.TRUTH_TABLE[truth_table_idx][2]
        ]

        # GPIO.output(IVPortCamera.select, pin_values[0])
        # GPIO.output(IVPortCamera.enable_pins[0], pin_values[1])
        # GPIO.output(IVPortCamera.enable_pins[1], pin_values[2])
        print(pin_values)

    def capture_image(self, filename: str = None) -> list:
        """
        capture method for IVPort
        iterates over the number of vameras

        :return: :func:`numpy.array` if filename not specified, otherwise list of files.
        :rtype: numpy.array or list
        """
        filenames = []
        st = time.time()
        import picamera
        import numpy as np
        try:
            with picamera.PiCamera() as camera:
                with picamera.array.PiRGBArray(camera) as _image:
                    camera.start_preview()
                    time.sleep(2)  # Camera warm-up time
                    self.set_camera_settings(camera)
                    w, h = camera.resolution
                    self._image = numpy.empty((h, w * len(IVPortCamera.TRUTH_TABLE), 3), dtype=numpy.uint8)
                    for c in range(0, len(IVPortCamera.TRUTH_TABLE)):
                        try:
                            ast = time.time()
                            IVPortCamera.switch(idx=c)
                            camera.capture(_image, 'rgb')
                            # _image = numpy.empty((camera.resolution[1], camera.resolution[0], 3), dtype=numpy.uint8)

                            if filename:
                                image_numbered = "{}-{}{}".format(os.path.splitext(filename)[0], str(c),
                                                                  os.path.splitext(filename)[-1])
                                filenames.append(self.encode_write_np_array(_image.array, image_numbered))
                                self.logger.debug(
                                    "Took {0:.2f}s to capture image #{1}".format(time.time() - ast, str(c)))

                            # setup the images
                            offset = c * w
                            self._image[0:h, offset: offset + w] = _image.array
                            self._image = cv2.cvtColor(self._image, cv2.COLOR_BGR2RGB)
                        except Exception as e:
                            self.logger.critical("Couldnt capture (IVPORT) with camera {} {}".format(str(c), str(e)))
                        _image.truncate(0)
                        time.sleep(0.1)
            self.logger.debug("Took {0:.2f}s to capture all images".format(time.time() - ast))
            if filename:
                return filenames
            else:
                return self._image
        except Exception as e:
            self.logger.error("Couldnt acquire picam: {}".format(str(e)))


"""
Threaded implementations
"""
#
# class ThreadedCamera(Camera, Thread):
#     """
#     threaded implementation of the Camera cclass.
#     """
#     def __init__(self, identifier, *args, **kwargs):
#         Thread.__init__(self, name=identifier)
#         self.daemon = True
#         print("Threaded started {}: {}".format(self.__class__, identifier))
#
#
# class ThreadedPiCamera(PiCamera, ThreadedCamera):
#     def __init__(self, identifier, *args, **kwargs):
#         super(ThreadedPiCamera, self).__init__(identifier, *args, **kwargs)
#         super(ThreadedCamera, self).__init__(identifier, *args, **kwargs)
#
#     def run(self):
#         super(PiCamera, self).run()
#
# class ThreadedGPCamera(GPCamera, ThreadedCamera):
#     def __init__(self, identifier, *args, **kwargs):
#         super(GPCamera, self).__init__(identifier, *args, **kwargs)
#         super(ThreadedCamera, self).__init__(identifier, *args, **kwargs)
#
#     def run(self):
#         super(GPCamera, self).run()
#
# class ThreadedIPCamera(IPCamera, ThreadedCamera):
#     def __init__(self, identifier, *args, **kwargs):
#         super(ThreadedIPCamera, self).__init__(identifier, *args, **kwargs)
#         super(ThreadedCamera, self).__init__(identifier, *args, **kwargs)
#
#     def run(self):
#         super(IPCamera, self).run()
#
# class ThreadedUSBCamera(USBCamera, ThreadedCamera):
#     def __init__(self, identifier, *args, **kwargs):
#         super(ThreadedUSBCamera, self).__init__(identifier, *args, **kwargs)
#         super(ThreadedCamera, self).__init__(identifier, *args, **kwargs)
#
#     def run(self):
#         super(USBCamera, self).run()
#
#
# class ThreadedIVPortCamera(IVPortCamera, ThreadedCamera):
#     def __init__(self, identifier, *args, **kwargs):
#         super(ThreadedIVPortCamera, self).__init__(identifier, *args, **kwargs)
#         super(ThreadedCamera, self).__init__(identifier, *args, **kwargs)
#
#     def run(self):
#         super(IVPortCamera, self).run()
#
