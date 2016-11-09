import datetime
import yaml
import logging.config
import os
import shutil
import time
import re
import numpy as np
import gphoto2cffi as gp
from urllib import request as urllib_request
from xml.etree import ElementTree
from collections import deque
from io import BytesIO

USBDEVFS_RESET = 21780

try:
    import cv2
except ImportError:
    print("Couldnt import cv2... no webcam capture")

import threading
from threading import Thread, Event

from libs.SysUtil import SysUtil
from gphoto2cffi.errors import GPhoto2Error

try:
    import picamera.array
except:
    pass

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)


def import_or_install(package, import_name=None, namespace_name=None):
    try:
        import importlib
        try:
            importlib.import_module(import_name or package)
        except ImportError:
            import pip
            print("Couldn't import package. installing package " + package)
            pip.main(['install', package])
        finally:
            globals()[namespace_name or import_name or package] = importlib.import_module(import_name or package)
    except Exception as e:
        print(
            "couldnt install or import {} from {} for some reason: {}".format(namespace_name or import_name or package,
                                                                              import_name or package, str(e)))


import_or_install("RPi.GPIO", namespace_name="GPIO")
import_or_install("Pillow", import_name="PIL.Image", namespace_name="Image")
import_or_install("picamera")


def _nested_lookup(key, document):
    """
    nested document lookup,
    works on dicts and lists
    :param key: string of key to lookup
    :param document: dict or list to lookup
    :return: yields item
    """
    if isinstance(document, list):
        for d in document:
            for result in _nested_lookup(key, d):
                yield result

    if isinstance(document, dict):
        for k, v in document.items():
            if k == key:
                yield v
            elif isinstance(v, dict):
                for result in _nested_lookup(key, v):
                    yield result
            elif isinstance(v, list):
                for d in v:
                    for result in _nested_lookup(key, d):
                        yield result


class Camera(object):
    accuracy = 3
    default_width, default_height = 640, 480
    file_types = ["CR2", "RAW", "NEF", "JPG", "JPEG", "PPM", "TIF", "TIFF"]
    output_types = ["tif", 'jpg']


    frame = None
    thread = None
    last_access = None

    def init_stream(self):
        """
        Initialises a video stream thread.
        :return:
        """
        if self.__class__.thread is None:
            # start background frame thread
            self.__class__.thread = threading.Thread(target=self._stream_thread)
            self.__class__.thread.start()
            # wait until frames start to be available
            while self.__class__.frame is None:
                time.sleep(0.01)

    def get_frame(self):
        """
        returns a frame from the stream thread.
        :return:
        """
        self.__class__.last_access = time.time()
        self.init_stream()
        return self.__class__.frame

    @classmethod
    def _stream_thread(cls):
        """
        boilerplate stream thread.
        override this with the correct method of opening anc closing the camera
        make sure to set cls.frame
        :return:
        """
        print("Unimplemented classmethod call: _stream_thread")
        print("You should not create a Camera object directly")

        def get_camera():
            """
            boilerplate
            :return: some camera object or context or whatever is meant to happen
            """
            pass

        with get_camera() as camera:
            # let camera warm up
            while True:
                cls.frame = camera.get_frame().read()
                # if there hasn't been any clients asking for frames in
                # the last 10 seconds stop the thread
                if time.time() - cls.last_access > 10:
                    break
        cls.thread = None

    def __init__(self, identifier: str=None, queue: deque=None, noconf: bool=False, **kwargs):
        """
        Initialiser for cameras...
        :param identifier: unique identified for this camera, MANDATORY
        :param queue: deque to push info into
        :param noconf: dont create a config, or watch anything. Used for temporarily streaming from a camera
        :param kwargs:
        """
        if queue is None:
            queue = deque(tuple(), 256)
        self.communication_queue = queue
        self.logger = logging.getLogger(identifier)
        self.stopper = Event()
        self.identifier = identifier

        self.failed = list()
        self._exif = dict()
        self._frame = None
        self._image = np.empty((Camera.default_width, Camera.default_height, 3), np.uint8)
        if not noconf:
            self.config_filename = SysUtil.identifier_to_ini(self.identifier)
            self.config = \
                self.camera_name = \
                self.interval = \
                self.spool_directory = \
                self.upload_directory = \
                self.begin_capture = \
                self.end_capture = \
                self.begin_capture = \
                self.end_capture = \
                self.current_capture_time = None
            self.re_init()

        self._image_size_list = [[5472, 3648],
                                 [1920, 1080],
                                 [1280, 720],
                                 [640, 480]]
        self._image_size = self._image_size_list[0]
        self._image_quality = 100
        self._focus_modes = ["AUTO", "MANUAL"]

        self._hfov_list = [71.664, 58.269, 47.670, 40.981, 33.177, 25.246, 18.126, 12.782, 9.217, 7.050, 5.82]
        self._vfov_list = [39.469, 33.601, 26.508, 22.227, 16.750, 13.002, 10.324, 7.7136, 4.787, 3.729, 2.448]
        self._hfov = self._vfov = None
        self._zoom_list = [50, 150, 250, 350, 450, 550, 650, 750, 850, 950, 1000]
        self._zoom_position = 800
        self._zoom_range = [30, 1000]
        self._focus_range = [-float("inf"), float("inf")]
        self._focus_mode = "MANUAL"
        self._focus_position = 0

    def re_init(self):
        """
        re-initialisation.
        this causes all the confiuration values to be reacquired, and a config to be recreated as valid if it is broken.
        :return:
        """
        self.logger.info("Re-init...")
        self.config = SysUtil.ensure_config(self.identifier)

        self.camera_name = self.config["camera"]["name"]
        self.interval = self.config.getint("timelapse", "interval")
        self.spool_directory = self.config["localfiles"]["spooling_dir"]
        self.upload_directory = self.config["localfiles"]["upload_dir"]
        self.begin_capture = datetime.time(0, 0)
        self.end_capture = datetime.time(23, 59)

        start_time_string = str(self.config['timelapse']['starttime'])
        start_time_string = start_time_string.replace(":", "")
        end_time_string = str(self.config['timelapse']['stoptime'])
        end_time_string = end_time_string.replace(":", "")
        try:
            start_time_string = start_time_string[:4]
            assert end_time_string.isdigit(), "Non numerical start time, {}".format(str(end_time_string))
            self.begin_capture = datetime.datetime.strptime(start_time_string, "%H%M").time()
        except Exception as e:
            self.logger.error("Time conversion error starttime - {}".format_map(str(e)))
        try:
            # cut string to max of 4.
            end_time_string = end_time_string[:4]
            assert end_time_string.isdigit(), "Non numerical end time, {}".format(str(end_time_string))
            self.end_capture = datetime.datetime.strptime(end_time_string, "%H%M").time()
        except Exception as e:
            self.logger.error("Time conversion error stoptime - {}".format(str(e)))

        try:
            if not os.path.exists(self.spool_directory):
                self.logger.info("Creating spoool dir {}".format(self.spool_directory))
                os.makedirs(self.spool_directory)
            else:
                shutil.rmtree(self.spool_directory)
                os.makedirs(self.spool_directory)

            if not os.path.exists(self.upload_directory):
                self.logger.info("Creating upload dir {}".format(self.upload_directory))
                os.makedirs(self.upload_directory)
        except Exception as e:
            self.logger.error("Creating directories {}".format(str(e)))

        self._exif = self.get_exif_fields()

        self.current_capture_time = datetime.datetime.now()

    @property
    def exif(self)->dict:
        """
        returns the current exif data.
        :return:
        """
        self._exif["Exif.Photo.DateTimeOriginal"] = datetime.datetime.now()
        return self._exif

    @property
    def image(self):
        return self._image

    @staticmethod
    def timestamp(tn: datetime.datetime)->str:
        """
        creates a properly formatted timestamp.
        :param tn: datetime to format to timestream timestamp string
        :return:
        """
        return  tn.strftime('%Y_%m_%d_%H_%M_%S')

    @staticmethod
    def time2seconds(t: datetime.datetime)->int:
        """
        converts a datetime to an integer of seconds since epoch
        """
        try:
            return int(t.timestamp())
        except:
            # the 'timestamp()' method is only implemented in python3.3`
            # this is an old compatibility thing
            return int(t.hour * 60 * 60 + t.minute * 60 + t.second)

    @property
    def timestamped_imagename(self)->str:
        """
        builds a timestamped image basename without extension from a datetime.
        :param time_now:
        :return: string image basename
        """
        return '{camera_name}_{timestamp}'.format(camera_name=self.camera_name,
                                                  timestamp=Camera.timestamp(self.current_capture_time))

    @property
    def time_to_capture(self)->bool:
        """
        filters out times for capture, returns True by default
        returns False if the conditions where the camera should NOT capture are met.
        :return:
        """
        current_naive_time = self.current_capture_time.time()

        if not self.config.getboolean("camera", "enabled"):
            # if the camera is disabled, never take photos
            return False

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

    def get_exif_fields(self)->dict:
        """
        get default fields for exif, this should be overriden and super-ed
        :return:
        """
        exif = dict()
        exif['Exif.Image.Make'] = "Make"
        exif['Exif.Image.Model'] = "Model"
        exif['Exif.Image.CameraSerialNumber'] = self.identifier
        return exif

    def _write_np_array(self, np_image_array: np.array, fn: str)->list:
        """
        takes a RGB numpy image array like the ones from cv2 and writes it to disk as a tif and jpg
        converts from rgb to bgr for cv2 so that the images save correctly
        :param np_image_array: 3 dimensional image array, x,y,rgb
        :param fn: filename
        :return:
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
                except:
                    self.logger.error("Couldnt write the appropriate metadata, {}".format(str(e)))
        return successes

    @staticmethod
    def _write_raw_bytes(image_bytesio: BytesIO, fn: str)->list:
        """
        Writes a BytesIO object to disk.
        :param image_bytesio: bytesio of an image.
        :param fn:
        :return:
        """
        with open(fn, 'wb') as f:
            f.write(image_bytesio.read())
            # no exif data when writing the purest bytes :-P
        return fn

    def _capture(self, filename=None):
        """
        internal camera capture method. override this method when creating a new type of camera.
        :param filename: image filename without extension
        :return: numpy image array if filename not specified, otherwise list of files.
        """
        return self._image

    def capture(self, filename=None):
        """
        public capture method. in testing override this to be capture_monkey:
        Camera.capture = Camera.capture_monkey
        :param filename:
        :return:
        """
        return self._capture(filename=filename)

    def capture_monkey(self, filename=None):
        """
        Simulates things going horribly wrong with the capture.
        Will sometimes return None, an empty list or an invalid filename.
        Sometimes will raise a generic Exception.
        The rest of the time it will capture a valid image.
        :param filename:
        :return:
        """
        self.logger.warning("Capturing with a naughty monkey.")
        import random
        s = random.uniform(0, 100)
        if s < 10:
            return None
        elif 10 <= s <= 20:
            return []
        elif 20 <= s <= 30:
            return ["Ooh ooh, ahh ahhh!"]
        elif 30 <= s <= 40:
            raise Exception("BANANAS")
        else:
            return self._capture(filename=filename)

    def stop(self):
        self.stopper.set()

    def focus(self):
        """
        override this method for manual autofocus trigger
        :return:
        """
        pass

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
        :return:
        """
        try:
            data = dict(
                name=self.camera_name,
                identifier=self.identifier,
                failed=self.failed,
                last_capture=int(self.current_capture_time.strftime("%s")))
            self.communication_queue.append(data)
            self.failed = list()
        except Exception as e:
            self.logger.error("thread communication error: {}".format(str(e)))

    def run(self):
        while True and not self.stopper.is_set():
            self.current_capture_time = datetime.datetime.now()
            # checking if enabled and other stuff
            if self.__class__.thread is not None:
                self.logger.critical("Camera live view thread is not closed, so camera lock cannot be acquired.")
                continue

            if self.time_to_capture:
                try:
                    start_capture_time = time.time()
                    raw_image = self.timestamped_imagename

                    self.logger.info("Capturing Image for {}".format(self.identifier))

                    files = self.capture(filename=os.path.join(self.spool_directory, raw_image))
                    # capture. if capture didnt happen dont continue with the rest.
                    if len(files) == 0:
                        self.failed.append(self.current_capture_time)
                        continue

                    if self.config.getboolean("ftp", "replace"):
                        st = time.time()
                        resize_t = 0.0
                        if self.config.getboolean("ftp", "resize"):
                            self._image = cv2.resize(self._image, (Camera.default_width, Camera.default_height),
                                                     interpolation=cv2.INTER_NEAREST)
                            resize_t = time.time() - st

                        cv2.putText(self._image,
                                    self.timestamped_imagename,
                                    org=(20, self._image.shape[0]-20),
                                    fontFace=cv2.FONT_HERSHEY_SIMPLEX,
                                    fontScale=1,
                                    color=(0, 0, 255),
                                    thickness=2,
                                    lineType=cv2.LINE_AA)

                        s = cv2.imwrite(os.path.join("/dev/shm", self.identifier + ".jpg"),
                                        cv2.cvtColor(self._image, cv2.COLOR_BGR2RGB))
                        shutil.copy(os.path.join("/dev/shm", self.identifier + ".jpg"),
                                    os.path.join(self.upload_directory, "last_image.jpg"))

                        self.logger.info("Resize {0:.3f}s, total: {0:.3f}s".format(resize_t, time.time() - st))

                    # copying/renaming for files
                    oldfiles = files[:]
                    files = []

                    for fn in oldfiles:
                        if type(fn) is list:
                            files.extend(fn)
                        else:
                            files.append(fn)

                    for fn in files:
                        try:
                            if self.config.getboolean("ftp", "timestamped"):
                                shutil.move(fn, self.upload_directory)
                                self.logger.info("Captured and stored - {}".format(os.path.basename(fn)))
                        except Exception as e:
                            self.logger.error("Couldn't move for timestamped: {}".format(str(e)))

                        try:
                            if os.path.isfile(fn):
                                os.remove(fn)
                        except Exception as e:
                            self.logger.error("Couldn't remove spooled: {}".format(str(e)))
                    self.logger.info("Total capture time: {0:.2f}s".format(time.time() - start_capture_time))
                    self.communicate_with_updater()
                except Exception as e:
                    self.logger.critical("Image Capture error - {}".format(str(e)))
            time.sleep(0.1)


    @property
    def image_quality(self):
        """
        gets the image quality
        :return:
        """
        return self._image_quality

    @image_quality.setter
    def image_quality(self, value):
        """
        sets the image quality
        :param value: percentage value of image quality
        :return:
        """
        self._image_quality = value


    @property
    def image_size(self):
        """
        gets the image resolution
        :return: image size
        """
        return self._image_size

    @image_size.setter
    def image_size(self, value):
        """
        sets the image resolution
        :param image_size: iterable of len 2 (width, height)
        :return:
        """
        assert type(value) in (list, tuple), "image size is not a list or tuple!"
        assert len(value) == 2, "image size doesnt have 2 elements width,height are required"
        value = list(value)
        # assert value in self._image_size_list, "image size not in available image sizes"
        self._image_size = value

    @property
    def zoom_position(self):
        """
        retrieves the current zoom position from the camera
        :return:
        """
        return self._zoom_position

    @zoom_position.setter
    def zoom_position(self, absolute_value):
        """
        sets the camera zoom position to an absolute value
        changes the hfov and vfov in some implementations
        :param absolute_value: absolute value to set zoom to
        :return:
        """
        assert (self._zoom_range is not None and absolute_value is not None)
        assert type(absolute_value) in (float, int)
        absolute_value = min(self._zoom_range[1], max(self._zoom_range[0], absolute_value))
        self._zoom_position = absolute_value
        # self._hfov = np.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        # self._vfov = np.interp(self._zoom_position, self.zoom_list, self.vfov_list)

    @property
    def zoom_range(self):
        """
        retrieves the available zoom range from the camera
        :return:
        """
        return self._zoom_range

    @zoom_range.setter
    def zoom_range(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == 2, "must be 2 values"
        self._zoom_range = list(value)

    @property
    def zoom_list(self):
        return self._zoom_list

    @zoom_list.setter
    def zoom_list(self, value):
        assert type(value) in (list, tuple), "Must be a list or tuple"
        assert len(value) > 1, "Must have more than one element"
        self._zoom_list = list(value)
        # it = iter(self._hfov_list)
        # self._hfov_list = [next(it, self._hfov_list[-1]) for _ in self._zoom_list]
        # it = iter(self._vfov_list)
        # self._vfov_list = [next(it, self._vfov_list[-1]) for _ in self._zoom_list]
        # self.zoom_position = self._zoom_position

    @property
    def focus_mode(self):
        """
        retrieves the current focus mode from the camera
        :return:
        """
        return self._focus_mode

    @focus_mode.setter
    def focus_mode(self, mode):
        self._focus_mode = mode

    @property
    def focus_position(self):
        """
        retrieves the current focus position from the camera
        :return: focus position or None
        """
        return None

    @focus_position.setter
    def focus_position(self, absolute_position):
        """
        sets the camera focus position to an absolute value
        :param absolute_position: focus position to set the camera to
        :return:
        """
        return self._focus_position

    @property
    def focus_range(self):
        return self._focus_range

    @property
    def hfov_list(self):
        return self._hfov_list

    @hfov_list.setter
    def hfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._hfov_list = list(value)

    @property
    def vfov_list(self):
        return self._vfov_list

    @vfov_list.setter
    def vfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._vfov_list = list(value)

    @property
    def hfov(self):
        self._hfov = np.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        return self._hfov

    @property
    def vfov(self):
        self._vfov = np.interp(self._zoom_position, self.zoom_list, self.vfov_list)
        return self._vfov

    @hfov.setter
    def hfov(self, value):
        self._hfov = value

    @vfov.setter
    def vfov(self, value):
        self._vfov = value

    def fov_override(self, fov):
        """
        overrides fov calculation from zoom position and lists to allow manual setting
        :param fov:
        :return:
        """
        self._zoom_position = 0
        self._zoom_list = [1,2]
        self._vfov_list = [fov[1], fov[1]]
        self._hfov_list = [fov[0], fov[0]]
        self.hfov = fov[0]
        self.vfov = fov[1]

    @property
    def status(self):
        """
        helper function to get a string of the current status.
        :return: informative string of zoom_pos zoom_range focus_pos focus_range
        """
        fmt_string = "zoom_pos:\t{}\nzoom_range:\t{}"
        fmt_string = "".join((fmt_string, "\nfocus_pos:\t{}\nfocus_range:\t{}"))
        return fmt_string.format(self.zoom_position, self.zoom_range, self.focus_position, self.focus_range)


class IPCamera(Camera):
    """
    IPCamera
    needs work to support both ymlconfig and normal configs.
    """

    def __init__(self, identifier=None, ip=None, ymlconfig=None, queue=None, **kwargs):

        if not ymlconfig:
            config = dict()

        if type(ymlconfig) is str:
            t = os.path.splitext(ymlconfig)[-1]
            with open(ymlconfig) as f:
                yaml.load(f.read())

        config = config.copy()
        self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger(self.getName())
        self.stopper = Event()
        self.identifier = identifier

        self.camera_name = \
            self.interval = \
            self.spool_directory = \
            self.upload_directory = \
            self.begin_capture = \
            self.end_capture = \
            self.begin_capture = \
            self.end_capture = \
            self.current_capture_time = None

        self.failed = list()
        self._image = np.empty((Camera.default_width, Camera.default_height, 3), np.uint8)
        self.re_init()

        self._image = None

        self._notified_unavailable = []

        #
        # self._HTTP_login = config.pop("HTTP_login","USER={user}&PWD={password}").format(
        #     user = user or config.pop("username", "admin"),
        #     password = password or config.pop("password", "admin"))
        #
        # self._url = config.pop("format_url","http://{ip}{command}&{HTTP_login}").format(
        #     ip = ip or config.pop("ip", None),
        #     HTTP_login = self._HTTP_login)

        format_str = config.pop("format_url", "http://{HTTP_login}@{ip}{command}")

        if format_str.startswith("http://{HTTP_login}@"):
            format_str = format_str.replace("{HTTP_login}@", "")
            password_mgr = urllib_request.HTTPPasswordMgrWithDefaultRealm()

            password_mgr.add_password(None,
                                      format_str.replace("{command}", "").format(
                                          ip=ip or config.get("ip", "192.168.1.7")),
                                      config.pop("username", "admin"),
                                      config.pop("password", "admin"))

            auth_handler = urllib_request.HTTPBasicAuthHandler(password_mgr)
            opener = urllib_request.build_opener(auth_handler)
            urllib_request.install_opener(opener)

        self._HTTP_login = config.pop("HTTP_login", "{user}:{password}").format(
            user=config.pop("username", "admin"),
            password=config.pop("password", "admin"))

        self._url = format_str.format(
            ip=ip or config.pop("ip", "192.168.1.7"),
            HTTP_login=self._HTTP_login,
            command="{command}")

        self._image_size_list = config.pop("image_size_list", [[1920, 1080], [1280, 720], [640, 480]])
        self._image_size = config.pop("image_size", self._image_size_list[0])
        image_quality = config.pop("image_quality", 100)
        self._image_quality = image_quality
        self._focus_modes = config.get("focus_modes", ["AUTO", "MANUAL"])

        self._hfov_list = config.pop("horizontal_fov_list",
                                     [71.664, 58.269, 47.670, 40.981, 33.177, 25.246, 18.126, 12.782, 9.217, 7.050,
                                      5.82])
        self._vfov_list = config.pop("vertical_fov_list",
                                     [39.469, 33.601, 26.508, 22.227, 16.750, 13.002, 10.324, 7.7136, 4.787, 3.729,
                                      2.448])
        self._hfov = self._vfov = None
        self._zoom_list = config.pop("zoom_list", [50, 150, 250, 350, 450, 550, 650, 750, 850, 950, 1000])
        self._zoom_position = config.pop("zoom_pos", 800)
        self._zoom_range = config.pop("zoom_range", [30, 1000])
        self._focus_range = config.pop("focus_range", [-float("inf"), float("inf")])

        # set commands from the rest of the config.
        self.commands = dict()
        self.parse_strings = dict()
        for k, v in config.items():
            if str(k).startswith("URL_"):
                self.commands[k] = v
            if str(k).startswith("RET_"):
                self.parse_strings[k] = v

        # set zoom position to fill hfov and vfov
        self.zoom_position = self._zoom_position

        self.image_quality = self.image_quality

        super(IPCamera, self).__init__(self, **kwargs)
        self.logger.info(self.status)

    def _read_stream(self, command_string, *args, **kwargs):
        """
        opens a url with the current HTTP_login string
        :type command_string: str
        :param command_string: url to go to with parameters
        :return: string of data returned from the camera
        """
        url = self._url.format(*args, command=command_string, **kwargs)
        if "&" in url and "?" not in url:
            url = url.replace("&", "?", 1)
        # print(url)
        try:
            stream = urllib_request.urlopen(url)
        except urllib_request.URLError as e:
            self.logger.error("Error reading stream {}".format(str(e)))
            return None
        return stream.read().strip()

    def _read_stream_raw(self, command_string, *args, **kwargs):
        """
        opens a url with the current HTTP_login string
        :type command_string: str
        :param command_string: url to go to with parameters
        :return: string of data returned from the camera
        """
        url = self._url.format(*args, command=command_string, **kwargs)
        if "&" in url and "?" not in url:
            url = url.replace("&", "?", 1)
        try:
            stream = urllib_request.urlopen(url)
        except urllib_request.URLError as e:
            self.logger.error("Error reading stream raw {}".format(str(e)))
            return None
        return stream.read()

    def _get_cmd(self, cmd):
        """
        gets a url command from the dict of available commands.
        :param cmd:
        :return:
        """
        cmd_str = self.commands.get(cmd, None)
        if not cmd_str and cmd_str not in self._notified_unavailable:
            self._notified_unavailable.append(cmd_str)
            self.logger.error("No command available for \"{}\"".format(cmd))
            return None
        if type(cmd_str) == str:
            cmd_str = tuple(cmd_str.split("!"))
            if len(cmd_str) == 1:
                cmd_str = cmd_str[0]
        return cmd_str

    def get_value_from_xml(self, message_xml, *args):
        """
        gets float, int or string values from a xml string where the key is the tag of the first element with value as
        text.
        returns a dict if more than 1 arg.
        returns single value if 1 arg, or None if single arg not found in xml.
        :param message_xml:
        :param args: list of keys to find values for.
        :return:
        """
        # assert (len(args) > 0, "No keys to search")
        root_element = ElementTree.fromstring(message_xml)
        return_values = {}
        for key in args:
            target_ele = root_element.find(key)
            if not target_ele:
                continue

            value = target_ele.text.replace(' ', '')
            if not value:
                continue

            types = [float, int, str]
            for t in types:
                try:
                    return_values[key] = t(value)
                except ValueError:
                    pass
            else:
                self.logger.error(
                    "Couldn't cast an xml element text attribute to str. What are you feeding the xml parser?")
        # return single arg
        if len(args) == 1 and len(return_values) == 1:
            return next(iter(return_values.values()))
        elif len(args) == 1:
            return None
        return return_values

    @staticmethod
    def get_value_from_stream(raw_text, *args):
        """
        parses a string returned from the camera by urlopen into a list
        :type raw_text: str to be parsed
        :param text: text to parse
        :param args: string keys to select
        :return: list of values or None if input text has no '=' chars or dict of key values if args
        """
        if raw_text is None:
            raw_text = ""
        multitexts = raw_text.splitlines()
        multitexts = [x.decode().split("=") for x in multitexts]
        multitexts = dict([(x[0], x[-1]) for x in multitexts])

        if len(args):
            return dict((k, multitexts[k]) for k in args if k in multitexts.keys())

        values = []

        def ap(v):
            try:
                a = float(v)
                values.append(a)
            except:
                values.append(a)

        for k, value in multitexts.items():
            value = re.sub("'", "", value)
            if ',' in value:
                va = value.split()
                for v in va:
                    ap(v)
            else:
                ap(value)

        return values

    def _capture(self, filename=None):
        """
        captures an image.
        it returns an np.array of the image
        if file_name is a string, it will same the image to disk.
        if file_name is None, it will save the file with a temporary file name.
        if file_name is either a string or None it will return the filename, not a np.array

        :param filename: file name to save as
        :return: numpy array, file name
        """
        st = time.time()
        cmd = self._get_cmd("URL_get_image")
        if not cmd:
            self.logger.error("No capture command, this is wrong...")
            return self._image

        url = self._url.format(command=cmd)
        for x in range(10):
            try:
                # fast method
                a = self._read_stream_raw(cmd)
                b = np.fromstring(a, np.uint8)
                barry = cv2.imdecode(b, cv2.IMREAD_COLOR)
                self._image = barry
                if filename:
                    self._write_np_array(self._image, filename)
                    self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                    return filename
                else:
                    self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                    return self._image
            except Exception as e:
                self.logger.error("Capture from network camera failed {}".format(str(e)))
            time.sleep(0.2)
        else:
            self.logger.error("All capture attempts (10) for network camera failed.")

    @property
    def image_quality(self):
        """
        gets the image quality
        :return:
        """
        return self._image_quality

    @image_quality.setter
    def image_quality(self, value):
        """
        sets the image quality
        :param value: percentage value of image quality
        :return:
        """
        assert (1 <= value <= 100)
        cmd = self._get_cmd("URL_get_image_quality")
        if cmd:
            self._read_stream(cmd.format(value))

    @property
    def image_size(self):
        """
        gets the image resolution
        :return: image size
        """

        cmd = self._get_cmd("URL_get_image_size")
        if cmd:
            key = None
            if type(cmd) is tuple:
                cmd, key = cmd
            stream = self._read_stream(cmd)
            output = self.get_value_from_stream(stream, key)
            if type(output) is dict:
                output = output.get(key, None)
            else:
                return self._image_size
            if output:
                if type(output) is list:
                    self._image_size = output
                else:
                    self._image_size = self._image_size_list[int(output) % len(self._image_size_list)]
        return self._image_size

    @image_size.setter
    def image_size(self, value):
        """
        sets the image resolution
        :param image_size: iterable of len 2 (width, height)
        :return:
        """
        assert type(value) in (list, tuple), "image size is not a list or tuple!"
        assert len(value) == 2, "image size doesnt have 2 elements width,height are required"
        value = list(value)
        assert value in self._image_size_list, "image size not in available image sizes"
        cmd = self._get_cmd("URL_set_image_size")
        if cmd:
            self._read_stream(cmd.format(width=value[0], height=value[1]))
            self._image_size = value

    @property
    def zoom_position(self):
        """
        retrieves the current zoom position from the camera
        :return:
        """
        cmd = self._get_cmd("URL_get_zoom")
        if cmd:
            try:
                stream_output = self._read_stream(cmd)
                value = self.get_value_from_stream(stream_output)
                if value:
                    self._zoom_position = value
            except:
                pass

        self._hfov = np.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        self._vfov = np.interp(self._zoom_position, self.zoom_list, self.vfov_list)
        return self._zoom_position

    @zoom_position.setter
    def zoom_position(self, absolute_value):
        """
        sets the camera zoom position to an absolute value
        :param absolute_value: absolute value to set zoom to
        :return:
        """
        cmd = self._get_cmd("URL_set_zoom")
        if cmd:
            assert (self._zoom_range is not None and absolute_value is not None)
            assert type(absolute_value) in (float, int)
            absolute_value = min(self._zoom_range[1], max(self._zoom_range[0], absolute_value))
            try:
                stream_output = self._read_stream(cmd.format(zoom=absolute_value))
                value = self.get_value_from_stream(stream_output)
                if value:
                    self._zoom_position = value
            except:
                pass
        else:
            self._zoom_position = absolute_value
        self._hfov = np.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        self._vfov = np.interp(self._zoom_position, self.zoom_list, self.vfov_list)

    @property
    def zoom_range(self):
        """
        retrieves the available zoom range from the camera
        :return:
        """
        cmd = self._get_cmd("URL_get_zoom_range")
        if not cmd:
            return self._zoom_range
        stream_output = self._read_stream(cmd)
        v = self.get_value_from_stream(stream_output)
        if v:
            self._zoom_range = v
        return self._zoom_range

    @zoom_range.setter
    def zoom_range(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == 2, "must be 2 values"
        self._zoom_range = list(value)

    @property
    def zoom_list(self):
        return self._zoom_list

    @zoom_list.setter
    def zoom_list(self, value):
        assert type(value) in (list, tuple), "Must be a list or tuple"
        assert len(value) > 1, "Must have more than one element"
        self._zoom_list = list(value)
        it = iter(self._hfov_list)
        self._hfov_list = [next(it, self._hfov_list[-1]) for _ in self._zoom_list]
        it = iter(self._vfov_list)
        self._vfov_list = [next(it, self._vfov_list[-1]) for _ in self._zoom_list]
        self.zoom_position = self._zoom_position

    @property
    def focus_mode(self):
        """
        retrieves the current focus mode from the camera
        :return:
        """
        cmd = self._get_cmd("URL_get_focus_mode")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        return self.get_value_from_stream(stream_output)

    @focus_mode.setter
    def focus_mode(self, mode):
        """
        sets the focus mode of the camera
        :type mode: str
        :param mode: focus mode of the camera. must be in self.focus_modes
        :return:
        """
        assert (self._focus_modes is not None)
        if mode.upper() not in self._focus_modes:
            print("Focus mode not in list of supported focus modes. YMMV.")
        cmd = self._get_cmd("URL_set_focus_mode")
        if cmd:
            self._read_stream(cmd.format(mode=mode.upper()))

    @property
    def focus_position(self):
        """
        retrieves the current focus position from the camera
        :return: focus position or None
        """
        cmd = self._get_cmd("URL_get_focus")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        result = self.get_value_from_stream(stream_output)
        return next(iter(result), float("inf"))

    @focus_position.setter
    def focus_position(self, absolute_position):
        """
        sets the camera focus position to an absolute value
        :param absolute_position: focus position to set the camera to
        :return:
        """
        cmd = self._get_cmd("URL_set_focus")
        if cmd:
            assert (self._focus_range is not None and absolute_position is not None)
            absolute_position = min(self._focus_range[1], max(self._focus_range[0], absolute_position))
            assert (self._focus_range[0] <= absolute_position <= self._focus_range[1])
            self._read_stream(format.format(focus=absolute_position))

    @property
    def focus_range(self):
        """
        retrieves a list of the focus type and range from the camera
        i.e. ["Motorized", 1029.0, 221.0]
        :return: [str:focus type, float:focus max, float:focus min]
        """
        cmd = self._get_cmd("URL_get_focus_range")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd)
        values = self.get_value_from_stream(stream_output)
        return values[2:0:-1]

    @property
    def hfov_list(self):
        return self._hfov_list

    @hfov_list.setter
    def hfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._hfov_list = list(value)

    @property
    def vfov_list(self):
        return self._vfov_list

    @vfov_list.setter
    def vfov_list(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == len(self._zoom_list), "must be the same length as zoom list"
        self._vfov_list = list(value)

    @property
    def hfov(self):
        self._hfov = np.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        return self._hfov

    @property
    def vfov(self):
        self._vfov = np.interp(self._zoom_position, self.zoom_list, self.vfov_list)
        return self._vfov

    @hfov.setter
    def hfov(self, value):
        self._hfov = value

    @vfov.setter
    def vfov(self, value):
        self._vfov = value

    @property
    def status(self):
        """
        helper function to get a string of the current status.
        :return: informative string of zoom_pos zoom_range focus_pos focus_range
        """
        fmt_string = "zoom_pos:\t{}\nzoom_range:\t{}"
        fmt_string = "".join((fmt_string, "\nfocus_pos:\t{}\nfocus_range:\t{}"))
        return fmt_string.format(self.zoom_position, self.zoom_range, self.focus_position, self.focus_range)

    def focus(self):
        """
        forces a refocus of the the camera
        :return:
        """
        cmd = self._get_cmd("URL_set_focus_mode")
        if not cmd:
            return None
        stream_output = self._read_stream(cmd.format(mode="REFOCUS"))
        return self.get_value_from_stream(stream_output)


class GPCamera(Camera):
    """
    Camera class
    other cameras inherit from this class.
    identifier and usb_address are NOT OPTIONAL
    """

    def __init__(self, identifier: str=None, usb_address: tuple=None, lock=None, **kwargs):
        """
        this needs to be fixed for multiple cameras.
        :param identifier: serialnumber of camera or None for next camera.
        :param usb_address: desired camera usb address. second to serialnumber, useful if recreating camera
        :param kwargs:
        """
        self._serialnumber = None
        self.lock = lock
        camera = None
        with self.lock:
            if identifier:
                self.identifier = identifier
                for cam in gp.list_cameras():
                    sn = cam.status.serialnumber
                    if sn in identifier:
                        camera = cam
                        break
                else:
                    raise IOError("Camera not available or connected")
            elif usb_address and not identifier:
                try:
                    camera = gp.Camera(bus=usb_address[0], device=usb_address[1])
                    sn = str(camera.status.serialnumber)
                    self.identifier = SysUtil.default_identifier(prefix=sn)
                except Exception as e:
                    raise IOError("Camera not found or not supported")
            else:
                for cam in gp.list_cameras():
                    try:
                        serialnumber = cam.status.serialnumber
                        sn = str(cam.status.serialnumber)
                        self.identifier = SysUtil.default_identifier(prefix=sn)
                        camera = cam
                        break
                    except:
                        pass
                else:
                    raise IOError("No cameras available")

            identifier = self.identifier
            self.usb_address = camera._usb_address
            self._serialnumber = camera.status.serialnumber

        super(GPCamera, self).__init__(identifier, **kwargs)

        self.exposure_length = self.config.get('camera', "exposure")

    def re_init(self):
        """
        re initialises the camera.
        :return:
        """
        super(GPCamera, self).re_init()
        self.logger.info("Camera detected at usb port {}:{}".format(*self.usb_address))
        self.exposure_length = self.config.getint("camera", "exposure")

    def get_exif_fields(self):
        """
        This is meant to get the exif fields for the image if we want to manually save them.
        This is incomplete.
        :return:
        """
        camera = self._get_camera()
        exif = super(GPCamera, self).get_exif_fields()
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
        return exif

    def _get_camera(self):
        with self.lock:
            try:
                camera = gp.Camera(bus=self.usb_address[0], device=self.usb_address[1])
                if self._serialnumber == camera.status.serialnumber:
                    self.logger.debug("Camera matched for {}:{}".format(*self.usb_address))
                    return camera
            except Exception as e:
                self.logger.info("Camera wasnt at the correct usb address or something: {}".format(str(e)))

            for camera in gp.list_cameras():
                try:
                    if camera.status.serialnumber == self._serialnumber:
                        return camera
                except Exception as e:
                    self.logger.info("Couldnt acquire lock for camera. {}".format(str(e)))
            else:
                raise FileNotFoundError("Camera cannot be found")

    def _capture(self, filename=None):
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
                            fn = (filename or os.path.splitext(image.filename)[0]) + os.path.splitext(image.filename)[-1]
                            if idx == 0:
                                self._image = cv2.imdecode(np.fromstring(image.read(), np.uint8), cv2.IMREAD_COLOR)
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
    def serial_number(self)->str:
        return self._serialnumber

    def focus(self):
        """
        this is meant to trigger the autofocus. currently not in use because it causes some distortion in the images.
        :return:
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
    def eos_serial_number(self)->str or None:
        """
        returns the eosserialnumber of supported cameras, otherwise the normal serialnumber
        :return:
        """
        camera = self._get_camera()
        sn = vars(camera.status).get("eosserialnumber", self.serial_number)
        camera.release()
        return sn

    def _config(self, field: str)->list:
        """
        searches for a field from the camera config.
        :param field: string to search
        :return: list of matching fields, should mostly be len 1
        """
        fields_found = []
        camera = self._get_camera()
        config = camera._get_config()
        camera.release()
        return list(_nested_lookup(field, config))


class USBCamera(Camera):
    """
    USB Camera Class
    """
    @classmethod
    def _stream_thread(cls):
        """
        usb camera stream thread.
        TODO: Needs to be aware of multiple cameras.
        :return:
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
            cls.frame = frame[1].tostring()
            # store frame

            # if there hasn't been any clients asking for frames in
            # the last 10 seconds stop the thread
            if time.time() - cls.last_access > 10:
                print("ThreadShutdown")
                break
        cls.thread = None

    def __init__(self, identifier, sys_number, **kwargs):
        """
        webcamera init. must have a sys_number (the 0 from /dev/video0) to capture from
        :param identifier:
        :param sys_number:
        :param kwargs:
        """
        # only webcams have a v4l sys_number.
        self.sys_number = int(sys_number)
        self.video_capture = None
        try:
            self.video_capture = cv2.VideoCapture()
        except Exception as e:
            self.logger.fatal("couldnt open video capture device on {}".format(self.sys_number))

        super(USBCamera, self).__init__(identifier, **kwargs)

    def re_init(self):
        """
        re-initialisation of webcamera
        todo: fix release of camera otherwise it could be locked forever.
        :return:
        """
        super(USBCamera, self).re_init()
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

    def stop(self):
        """
        releases the video device and stops the camera thread
        :return:
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
        :return:
        """
        try:
            if not self.video_capture:
                self.video_capture = cv2.VideoCapture()

            if not self.video_capture.isOpened():
                if not self.video_capture.open(self.sys_number):
                    raise IOError("VideoCapture().open({}) failed.".format(self.sys_number))
        except Exception as e:
            self.logger.error("Capture device could not be opened {}".format(str(e)))

    def _capture(self, filename=None):
        """
        :param filename: filename to output
        :return:
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
                filenames = self._write_np_array(self._image, filename)
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
    def _stream_thread(cls):
        """
        picamera streaming thread target.
        :return:
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
                    cls.frame = stream.read()

                    # reset stream for next frame
                    stream.seek(0)
                    stream.truncate()

                    # if there hasn't been any clients asking for frames in
                    # the last 10 seconds stop the thread
                    time.sleep(0.01)
                    if time.time() - cls.last_access > 1:
                        break
        except Exception as e:
            print("Couldnt acquire camera")
        print("Closing Thread")
        cls.thread = None

    def set_camera_settings(self, camera):
        """
        sets the settings for the camera
        :param camera: picamera instance
        :return:
        """
        try:
            camera.resolution = camera.MAX_RESOLUTION
            if self.config.has_option("camera", "width") and self.config.has_option("camera", "height"):
                camera.resolution = (self.config.getint("camera", "width"),
                                     self.config.getint("camera", "height"))
            if self.config.has_option("camera", "shutter_speed"):
                camera.shutter_speed = self.config.getfloat("camera", "shutter_speed")
            if self.config.has_option("camera", "iso"):
                camera.iso = self.config.getint("camera", "iso")

        except Exception as e:
           self.logger.error("error setting picamera settings: {}".format(str(e)))

    def _capture(self, filename: str=None):
        st = time.time()
        try:
            with picamera.PiCamera() as camera:
                with picamera.array.PiRGBArray(camera) as output:
                    time.sleep(2)  # Camera warm-up time
                    self.set_camera_settings(camera)
                    time.sleep(0.2)
                    self._image = np.empty((camera.resolution[1], camera.resolution[0], 3), dtype=np.uint8)
                    camera.capture(output, 'rgb')
                    self._image = output.array
            if filename:
                filenames = self._write_np_array(self._image, filename)
                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                return filenames
            else:
                self.logger.debug("Took {0:.2f}s to capture".format(time.time() - st))
                return self._image
        except Exception as e:
            self.logger.critical("EPIC FAIL, trying other method. {}".format(str(e)))

class IVPortCamera(PiCamera):
    """
    IVPort class for multiple capture.
    the 4 tags on the IVport are setout below.
    """
    current_camera_index = 0

    # these are for the video streaming
    select = 7

    enable_pinsA = [11, 12]
    enable_pinsB = [15, 16]
    enable_pinsC = [21, 22]
    enable_pinsD = [23, 24]

    enable_pins = enable_pinsB

    TRUTH_TABLE = [[False, False, True],
                   [True, False, True],
                   [False, True, False],
                   [True, True, False]]

    def __init__(self, identifier: str=None, queue: deque=None, camera_number: int=None, **kwargs):
        """
        special __init__ for the IVport to set the gpio enumeration
        This controls which gpio are on or off to select the camera
        :param identifier:
        :param queue:
        :param kwargs:
        """
        if camera_number is None:
            super(IVPortCamera, self).__init__(identifier=identifier, queue=queue, **kwargs)
        else:
            self.__class__.current_camera_index = camera_number
            IVPortCamera.switch(idx=self.__class__.current_camera_index)

    def setup(self):
        """
        sets up gpio for IVPort
        :return:
        """
        super(IVPortCamera, self).setup()
        # switch to the current camera index.
        IVPortCamera.switch(idx=self.__class__.current_camera_index)

    @classmethod
    def switch(cls, idx: int=None):
        """
        switches the IVPort to a new camera
        with no index, switches to the next camera, looping around from the beginning
        :param idx: index to switch the camera to (optional)
        :return:
        """
        time.sleep(1)

        cls.current_camera_index += 1
        if idx is not None:
            cls.current_camera_index = idx

        cls.current_camera_index %= len(IVPortCamera.TRUTH_TABLE)
        print("Switching to camera {}".format(idx))
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(IVPortCamera.select, GPIO.OUT)
        GPIO.setup(IVPortCamera.enable_pins[0], GPIO.OUT)
        GPIO.setup(IVPortCamera.enable_pins[1], GPIO.OUT)

        pin_values = [
            IVPortCamera.TRUTH_TABLE[cls.current_camera_index][0],
            IVPortCamera.TRUTH_TABLE[cls.current_camera_index][1],
            IVPortCamera.TRUTH_TABLE[cls.current_camera_index][2]
        ]

        GPIO.output(IVPortCamera.select, pin_values[0])
        GPIO.output(IVPortCamera.enable_pins[0], pin_values[1])
        GPIO.output(IVPortCamera.enable_pins[1], pin_values[2])
        print(pin_values)

    def _capture(self, filename: str=None)->list:
        """
        capture method for IVPort
        iterates over the number of vameras
        :param filename: filename to capture to.
        :return:
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
                    self._image = np.empty((h, w*len(IVPortCamera.TRUTH_TABLE), 3), dtype=np.uint8)
                    for c in range(0, len(IVPortCamera.TRUTH_TABLE)):
                        try:
                            ast = time.time()
                            IVPortCamera.switch(idx=c)
                            camera.capture(_image, 'rgb')
                            # _image = np.empty((camera.resolution[1], camera.resolution[0], 3), dtype=np.uint8)

                            if filename:
                                image_numbered = "{}-{}{}".format(os.path.splitext(filename)[0], str(c),
                                                                  os.path.splitext(filename)[-1])
                                filenames.append(self._write_np_array(_image.array, image_numbered))
                                self.logger.debug(
                                    "Took {0:.2f}s to capture image #{1}".format(time.time() - ast, str(c)))

                            # setup the images
                            offset = c*w
                            self._image[0:h, offset: offset+w] = _image.array
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


class ThreadedCamera(Thread):
    def __init__(self, *args, **kwargs):
        if hasattr(self, "identifier"):
            Thread.__init__(self, name=self.identifier)
        else:
            Thread.__init__(self)

        print("Threaded startup")
        # super(self.__class__, self).__init__(*args, **kwargs)
        self.daemon = True
        if hasattr(self, "config_filename") and hasattr(self, "re_init"):
            SysUtil().add_watch(self.config_filename, self.re_init)


class ThreadedGPCamera(ThreadedCamera, GPCamera):
    def __init__(self, *args, **kwargs):
        GPCamera.__init__(self, *args, **kwargs)
        super(ThreadedGPCamera, self).__init__(*args, **kwargs)

    def run(self):
        super(GPCamera, self).run()


class ThreadedIPCamera(ThreadedCamera, IPCamera):
    def __init__(self, *args, **kwargs):
        IPCamera.__init__(self, *args, **kwargs)
        super(ThreadedIPCamera, self).__init__(*args, **kwargs)

    def run(self):
        super(IPCamera, self).run()

class ThreadedUSBCamera(ThreadedCamera, USBCamera):
    def __init__(self, *args, **kwargs):
        USBCamera.__init__(self, *args, **kwargs)
        super(ThreadedUSBCamera, self).__init__(*args, **kwargs)

    def run(self):
        super(USBCamera, self).run()

class ThreadedPiCamera(ThreadedCamera, PiCamera):
    def __init__(self, *args, **kwargs):
        PiCamera.__init__(self, *args, **kwargs)
        super(ThreadedPiCamera, self).__init__(*args, **kwargs)


    def run(self):
        super(PiCamera, self).run()

class ThreadedIVPortCamera(ThreadedCamera, IVPortCamera):
    def __init__(self, *args, **kwargs):
        IVPortCamera.__init__(self, *args, **kwargs)
        super(ThreadedIVPortCamera, self).__init__(*args, **kwargs)

    def run(self):
        super(IVPortCamera, self).run()
