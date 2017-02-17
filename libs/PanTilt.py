import numpy
import time
import logging.config
from collections import deque
from threading import Thread
import requests
from requests.auth import HTTPBasicAuth
from xml.etree import ElementTree

logging.config.fileConfig("logging.ini")
logging.getLogger("paramiko").setLevel(logging.WARNING)


class PanTilt(object):
    """
    Control J-Systems PTZ

    For new system or new firmware, the system needs calibration as follows:
    - Open URL of the PTZ on a web browser
    - Click on "Calibration" tab, enter username and password if necessary
    - On Calibration window, click on "Open-loop" and then "Set Mode"
    - Use joystick controller to rotate the pan axis to minimum position
    - Click on 'Pan Axis Min' line, enter '2.0', and click "Set Calibration"
    - Use joystick controller to rotate the pan axis to maximum position
    - Click on 'Pan Axis Max' line, enter '358.0', and click "Set Calibration"
    - Use joystick controller to rotate the tilt axis to minimum position
    - Click on 'Tilt Axis Min' line, enter '-90.0', and click "Set Calibration"
    - Use joystick controller to rotate the tilt axis to maximum position
    - Click on 'Tilt Axis Max' line, enter '30.0', and click "Set Calibration"
    - Click on "Closed-loop" and then "Set Mode"
    - Close Calibration window
    """

    def __init__(self, ip=None, user=None, password=None, config=None, queue=None):
        self.communication_queue = deque(tuple(), 256) if queue is None else queue
        self.logger = logging.getLogger("PanTilt")
        if not config:
            config = dict()
        config = config.copy()

        self.command_urls = config.get('urls', {})
        self.return_keys = config.get('keys', {})
        self._notified = []
        self.return_parser = config.get("return_parser", "plaintext")
        format_str = config.get("format_url", "http://{HTTP_login}@{ip}{command}")

        self.auth_object = None
        if format_str.startswith("http://{HTTP_login}@"):
            format_str = format_str.replace("{HTTP_login}@", "")
            self.auth_object = HTTPBasicAuth(user or config.get("username", "admin"),
                                             password or config.get("password", "admin"))

        self._HTTP_login = config.get("HTTP_login", "{user}:{password}").format(
            user=user or config.get("username", "admin"),
            password=password or config.get("password", "admin"))

        self._url = format_str.format(
            ip=ip or config.get("ip", "192.168.1.101:81"),
            HTTP_login=self._HTTP_login,
            command="{command}")

        self._pan_tilt_scale = config.get("scale", 10.0)
        self._pan_range = list(config.get("pan_range", [0, 360]))
        self._tilt_range = list(config.get("tilt_range", [-90, 30]))
        self._position = [0, 0]
        self._pan_range.sort()
        self._tilt_range.sort()


        self._zoom_position = config.get("zoom", 800)
        self._zoom_range = config.get("zoom_range", [30, 1000])
        self.zoom_position = self._zoom_position
        # set zoom position to fill hfov and vfov
        # need to set this on camera.
        #     self._hfov = numpy.interp(self._zoom_position, self.zoom_list, self.hfov_list)
        #     self._vfov = numpy.interp(self._zoom_position, self.zoom_list, self.vfov_list)
        self._accuracy = config.get("accuracy", 0.5)

        self._rounding = len(str(float(self._accuracy)).split(".")[-1].replace("0", ""))

        time.sleep(0.2)

        self.logger.info("pantilt:".format(self.position))

    def communicate_with_updater(self):
        """
        communication member. This is meant to send some metadata to the updater thread.
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

    def _make_request(self, command_string, *args, **kwargs):
        """
        makes a generic request formatting the command string and applying the authentication.

        :param command_string:
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
        except Exception as e:
            self.logger.error("Some exception got raised {}".format(str(e)))
            return
        if response.status_code not in [200, 204]:
            self.logger.error("[{}] - {}\n{}".format(str(response.status_code), str(response.reason), str(response.url)))
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

        :type command_string: str
        :param command_string: url to go to with parameters
        :return: string of data returned from the camera
        """
        response = self._make_request(command_string, *args, **kwargs)
        if response is None:
            return
        return response.content

    def _get_cmd(self, cmd):
        cmd_str = self.command_urls.get(cmd, None)
        if not cmd_str and cmd_str not in self._notified:
            self.logger.error("No command available for \"{}\"".format(cmd))
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
        illegal = [b'\n', b'\t', b'\r',
                   b"<CPStatusMsg>", b"</CPStatusMsg>", b"<Text>",
                   b"</Text>", b"<Type>Info</Type>", b"<Type>Info",
                   b"Info</Type>", b"</Type>", b"<Type>"]
        for ill in illegal:
            message_xml = message_xml.replace(ill, b"")

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
            line = line.replace("= ","=").replace(" =","=").strip()
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
        gets a value from some text, based on what kind of parser this object uses (xml or plaintext)

        :param stream: text data to search
        :type stream: str
        :param keys: list of keys to search for the values of
        :return: dict of key: value pairs
        :rtype: dict
        """
        if stream is None: return
        if len(keys) is 0: return
        if self.return_parser == 'plaintext':
            return self.get_value_from_plaintext(stream, *keys)
        elif self.return_parser == 'xml':
            return self.get_value_from_xml(stream, *keys)
        else:
            return None

    def pan_step(self, direction, n_steps):
        """
        pans by step, steps must be less than or equal to 127

        :type n_steps: int
        :type direction: str
        :param direction:
        :param n_steps: integer <= 127. number of steps
        :return:
        """
        assert (abs(n_steps) <= 127)
        cmd, key = self._get_cmd("pan_step")
        if not cmd:
            return

        amt = -n_steps if direction.lower() == "left" else n_steps
        stream = self._read_stream(cmd.format(pan=amt))
        return self.get_value_from_stream(stream, *key)

    def tilt_step(self, direction, n_steps):
        """
        tilts by step, steps must be less than or equal to 127

        :type n_steps: int
        :type direction: str
        :param direction:
        :param n_steps: integer <= 127. number of steps
        :return:
        """
        assert (abs(n_steps) <= 127)
        amt = -n_steps if direction.lower() == "down" else n_steps

        cmd, key = self._get_cmd("tilt_step")
        if not cmd:
            return
        stream = self._read_stream(cmd.format(tilt=amt))

        return self.get_value_from_stream(stream, *key)

    @property
    def zoom_position(self):
        """
        gets zoom position.

        :getter: from camera.
        :setter: to camera.
        :rtype: tuple
        """
        cmd, keys = self._get_cmd("get_zoom")
        if cmd:
            try:
                stream_output = self._read_stream(cmd)
                self._zoom_position = self.get_value_from_stream(stream_output, keys) or self._zoom_position
            except:
                pass
        return self._zoom_position

    @zoom_position.setter
    def zoom_position(self, absolute_value):
        cmd, keys = self._get_cmd("set_zoom")
        if cmd:
            assert (self._zoom_range is not None and absolute_value is not None)
            assert type(absolute_value) in (float, int)
            absolute_value = min(self._zoom_range[1], max(self._zoom_range[0], absolute_value))
            try:
                stream_output = self._read_stream(cmd.format(zoom=absolute_value))
                value = self.get_value_from_stream(stream_output, *keys)
                if value:
                    self._zoom_position = value
            except:
                pass
        else:
            self._zoom_position = absolute_value

    @property
    def zoom_range(self):
        """
        Range of zoom for the camera.

        :getter: from camera.
        :setter: cached.
        :rtype: tuple
        """
        cmd,key = self._get_cmd("get_zoom_range")
        if not cmd:
            return self._zoom_range
        stream_output = self._read_stream(cmd)
        self._zoom_range = self.get_value_from_stream(stream_output, *key) or self._zoom_range
        return self._zoom_range

    @zoom_range.setter
    def zoom_range(self, value):
        assert type(value) in (list, tuple), "must be either list or tuple"
        assert len(value) == 2, "must be 2 values"
        self._zoom_range = list(value)

    @property
    def zoom_list(self) -> list:
        """
        List of zoom value intervals.

        Setting this also affects the state of other related variables.

        :getter: cached.
        :setter: recalculates, recalculates fov lists, resets zoom_position.
        :rtype: list
        """
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
    def position(self):
        """
        gets the current pan/tilt position.

        :return: tuple of current pan/tilt values
        :rtype: tuple(float, float)
        """
        cmd, keys = self._get_cmd("get_pan_tilt")
        if not cmd:
            return
        p = [None]
        try:
            output = self._read_stream(cmd)
            values = self.get_value_from_stream(output, *keys)
            p = tuple(values.get(k, None) for k in keys)
        except Exception as e:
            self.logger.error("ERROR: {}".format(str(e)))

        if not any(p):
            return self._position
        else:
            self._position = p
        return self._position

    def _get_pos(self):
        """
        slightly faster and less robust method of getting the position.

        :return: tuple of current pan/tilt values
        :rtype: tuple(float, float)
        """
        cmd, keys = self._get_cmd("get_pan_tilt")
        if cmd is None:
            return

        output = self._read_stream(cmd)
        values = self.get_value_from_stream(output, *keys)
        p = tuple(values.get(k, None) for k in keys)
        if not any(p):
            return None
        return p

    @position.setter
    def position(self, position=(None, None)):
        """
        Sets the absolute pan/tilt position in degrees.
        float degree values are floored to int.

        :type position: tuple
        :param position: absolute degree value for pan,tilt as (pan,tilt)
        """
        pan_degrees, tilt_degrees = position
        start_pos = self._get_pos()

        if not start_pos:
            return
        cmd, keys = self._get_cmd("set_pan_tilt")
        if not cmd:
            return

        if pan_degrees is None:
            pan_degrees = start_pos[0]
        if tilt_degrees is None:
            tilt_degrees = start_pos[1]

        pan_degrees, tilt_degrees = round(pan_degrees, 1), round(tilt_degrees, 1)

        pd = min(self._pan_range[1], max(self._pan_range[0], pan_degrees))
        td = min(self._tilt_range[1], max(self._tilt_range[0], tilt_degrees))

        diff = abs(self._position[0] - pd) + abs(self._position[1] - td)
        if diff <= self._accuracy:
            return

        if td != tilt_degrees or pd != pan_degrees:
            self.logger.error("hit pantilt limit")
            self.logger.error("{} [{}] {} ....... {} [{}] {}".format(
                self._pan_range[0], pan_degrees, self._pan_range[1],
                self._tilt_range[0], tilt_degrees, self._tilt_range[1]))

        pan_degrees, tilt_degrees = pd, td
        cmd = cmd.format(pan=pan_degrees * self._pan_tilt_scale,
                         tilt=tilt_degrees * self._pan_tilt_scale)

        for x in range(120):
            try:
                text = self._read_stream(cmd)
                if not text: # this breaks the next part because some ptzs return no-content on change.
                    break
                etr = ElementTree.fromstring(text)
                ele = (etr.findall(".//Type") or [None])[0]
                if getattr(ele, "text", None) == "Info":
                    print("")
                    break
                if x == 0:
                    print("Waiting on ptz.", end="")
                else:
                    print(".", end="")

                time.sleep(0.1)
            except Exception as e:
                self.logger.error("ERROR: {}".format(str(e)))
                time.sleep(1)

        else:
            self.logger.error("Couldn't set the pantilt position.")
            self.logger.error(self._read_stream(cmd))

        # loop until within 1 degree
        pan_pos, tilt_pos = None, None
        for _ in range(120):
            time.sleep(0.05)

            p = self._get_pos()
            if not p:
                continue
            pan_pos, tilt_pos = p
            pan_diff = abs(pan_pos - pan_degrees)
            tilt_diff = abs(tilt_pos - tilt_degrees)
            if pan_diff <= self._accuracy and tilt_diff <= self._accuracy:
                break
        else:
            self.logger.warning("Warning: pan-tilt fails to move to correct location")
            self.logger.warning("  Desired: pan_pos={}, tilt_pos={}".format(pan_degrees, tilt_degrees))
            self.logger.warning("  Current: pan_pos={}, tilt_pos={}".format(pan_pos, tilt_pos))

        # loop until smallest distance is reached
        for _ in range(0, 100):
            time.sleep(0.05)

            p = self._get_pos()
            if not p:
                continue
            pan_pos, tilt_pos = p

            pan_diff_new = abs(pan_pos - pan_degrees)
            tilt_diff_new = abs(tilt_pos - tilt_degrees)
            if pan_diff_new >= pan_diff or tilt_diff_new >= tilt_diff:
                break
            else:
                pan_diff = pan_diff_new
                tilt_diff = tilt_diff_new

        pn = self._position
        self._position = self.position
        # print("moved {}° | {}°".format(round(pd-pn[0], self._rounding), round(td-pn[1], self._rounding)))

    @property
    def scale(self):
        """
        scale of the pantilt, every operation is multiplied by this value (except zoom).

        :return: the scale
        :rtype: float
        """
        return self._pan_tilt_scale

    @scale.setter
    def scale(self, value):
        self._pan_tilt_scale = value

    @property
    def pan(self):
        """
        pan position

        :return: pan position
        :rtype: float
        """
        return self.position[0]

    @pan.setter
    def pan(self, value):
        self.position = (value, None)

    @property
    def pan_range(self):
        """
        the range of panning

        :return: list of [min, max] pan range
        :rtype: list
        """
        return self._pan_range

    @pan_range.setter
    def pan_range(self, value):
        assert type(value) in (list, tuple), "must be a list or tuple"
        assert len(value) == 2, "must have 2 elements"
        self._pan_range = sorted(list(value))

    @property
    def tilt(self):
        """
        see :func:`PanTilt.pan`
        """
        return self.position[1]

    @tilt.setter
    def tilt(self, value):
        self.position = (None, value)

    @property
    def tilt_range(self):
        """
        see :func:`PanTilt.pan_range`
        """
        return self._tilt_range

    @tilt_range.setter
    def tilt_range(self, value):
        assert type(value) in (list, tuple), "must be a list or tuple"
        assert len(value) == 2, "must have 2 elements"
        self._tilt_range = sorted(list(value))

    def hold_pan_tilt(self, state):
        """
        unknown, presumably holds the pan-tilt in one place.
        doesnt work...

        :param state: ? beats me.
        :return: whatever this does reads something?
        """
        cmd_str = "/Calibration.xml?Action=0" if state is True else "/Calibration.xml?Action=C"
        output = self._read_stream(cmd_str)
        # apparently this was left here?
        print(output)
        return self.get_value_from_stream(output, "Text")

    @property
    def PCCWLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "PCCWLS")

    @property
    def PCWLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "PCWLS")

    @property
    def TDnLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "TDnLS")

    @property
    def TUpLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "TUpLS")

    @property
    def battery_voltage(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "BattV")

    @property
    def heater(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "Heater")

    @property
    def temp_f(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "Temp")

    @property
    def list_state(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "ListState")

    @property
    def list_index(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "ListIndex")

    @property
    def control_mode(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "CtrlMode")

    @property
    def auto_patrol(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "AutoPatrol")

    @property
    def dwell(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_stream(output, "Dwell")


class ThreadedPTZ(Thread):
    def __init__(self, *args, **kwargs):
        if hasattr(self, "identifier"):
            Thread.__init__(self, name=self.identifier)
        else:
            Thread.__init__(self)

        print("Threaded startup")
        super(ThreadedPTZ, self).__init__(*args, **kwargs)
        self.daemon = True


class ThreadedPanTilt(ThreadedPTZ, PanTilt):
    def __init__(self, *args, **kwargs):
        self.identifier = "J-Systems PanTilt"
        PanTilt.__init__(self, *args, **kwargs)
        super(ThreadedPanTilt, self).__init__(*args, **kwargs)

    def run(self):

        super(PanTilt, self).run()
