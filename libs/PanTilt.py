import yaml
import time
import logging.config
from collections import deque
from threading import Thread
from urllib import request as urllib_request
from xml.etree import ElementTree

try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

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

        self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger("PanTilt")
        if not config:
            config = dict()
        config = config.copy()

        self._notified = []
        format_str = config.pop("format_url", "http://{HTTP_login}@{ip}{command}")

        if format_str.startswith("http://{HTTP_login}@"):
            format_str = format_str.replace("{HTTP_login}@", "")

            password_mgr = urllib_request.HTTPPasswordMgrWithDefaultRealm()
            password_mgr.add_password(None,
                                      format_str.replace("{command}", "").format(
                                          ip=ip or config.get("ip", "192.168.1.101:81")),
                                      user or config.pop("username", "admin"),
                                      password or config.pop("password", "admin"))
            auth_handler = urllib_request.HTTPBasicAuthHandler(password_mgr)
            opener = urllib_request.build_opener(auth_handler)
            urllib_request.install_opener(opener)

        self._HTTP_login = config.pop("HTTP_login", "{user}:{password}").format(
            user=user or config.pop("username", "admin"),
            password=password or config.pop("password", "admin"))

        self._url = format_str.format(
            ip=ip or config.pop("ip", "192.168.1.101:81"),
            HTTP_login=self._HTTP_login,
            command="{command}")

        self._pan_tilt_scale = config.pop("pan_tilt_scale", 10.0)
        self._pan_range = list(config.pop("pan_range", [0, 360]))
        self._tilt_range = list(config.pop("tilt_range", [-90, 30]))

        self._pan_range.sort()
        self._tilt_range.sort()

        self._accuracy = config.pop("accuracy", 0.5)
        self._rounding = len(str(float(self._accuracy)).split(".")[-1].replace("0", ""))
        self.commands = dict()
        self.parse_strings = dict()
        for k, v in config.items():
            if str(k).startswith("URL_"):
                self.commands[k] = v
            if str(k).startswith("RET_"):
                self.parse_strings[k] = v

        time.sleep(0.2)

        self.logger.info("pantilt:".format(self.position))

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
        try:
            stream = urllib_request.urlopen(url)
        except urllib_request.URLError as e:
            print(e)
            return None

        e = stream.read().strip()
        return e

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
            print(e)
            return None
        return stream.read()

    def _get_cmd(self, cmd):
        cmd_str = self.commands.get(cmd, None)
        if not cmd_str and cmd_str not in self._notified:
            print("No command available for \"{}\"".format(cmd))
            self._notified.append(cmd_str)
            return None
        if type(cmd_str) == str:
            cmd_str = tuple(cmd_str.split("!"))
            if len(cmd_str) == 1:
                cmd_str = cmd_str[0]
        return cmd_str

    @staticmethod
    def get_value_from_xml(message_xml, *args):
        """
        gets float, int or string values from a xml string where the key is the tag of the first element with value as
        text.
        returns a dict if more than 1 arg.
        returns single value if 1 arg, or None if single arg not found in xml.
        :param message_xml:
        :param args: list of keys to find values for.
        :return:
        """
        assert (len(args) > 0, "No keys to search")
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

        return_values = {}
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

        # return single arg
        if len(args) == 1 and len(return_values) == 1:
            return next(iter(return_values.values()))
        elif len(args) == 1:
            return None
        return return_values

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
        cmd = self._get_cmd("URL_pan_step")
        if cmd and type(cmd) is tuple:
            amt = -n_steps if direction.lower() == "left" else n_steps
            cmd, key = cmd
            stream = self._read_stream(cmd.format(pan=amt))
            return self.get_value_from_xml(stream, key)
        return None

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

        cmd = self._get_cmd("URL_tilt_step")
        if not cmd or type(cmd) is not tuple:
            return None

        cmd, key = cmd
        stream = self._read_stream(cmd.format(tilt=amt))

        return self.get_value_from_xml(stream, key)

    @property
    def position(self):
        """
        gets the current pan/tilt position.
        :return: tuple (pan, tilt)
        """
        cmd = self._get_cmd("URL_get_pan_tilt")
        if not cmd:
            return None
        keys = ["PanPos", "TiltPos"]
        if type(cmd) is tuple:
            cmd, keys = cmd[0], cmd[1:]
        try:

            output = self._read_stream(cmd)
            values = self.get_value_from_xml(output, *keys)
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
        :return:
        """
        cmd = self._get_cmd("URL_get_pan_tilt")
        if not cmd:
            return None
        keys = ["PanPos", "TiltPos"]
        if type(cmd) is tuple:
            cmd, keys = cmd[0], cmd[1:]

        output = self._read_stream(cmd)
        values = self.get_value_from_xml(output, *keys)
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
        :return:
        """
        pan_degrees, tilt_degrees = position
        start_pos = self._get_pos()

        if not start_pos:
            return
        cmd = self._get_cmd("URL_set_pan_tilt")
        if not cmd:
            return
        if type(cmd) is tuple:
            cmd, keys = cmd[0], cmd[1:]

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
                etr = ElementTree.fromstring(self._read_stream(cmd))
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

        time.sleep(0.1)
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
        return self._pan_tilt_scale

    @scale.setter
    def scale(self, value):
        self._pan_tilt_scale = value

    @property
    def pan(self):
        return self.position[0]

    @pan.setter
    def pan(self, value):
        self.position = (value, None)

    @property
    def pan_range(self):
        return self._pan_range

    @pan_range.setter
    def pan_range(self, value):
        assert type(value) in (list, tuple), "must be a list or tuple"
        assert len(value) == 2, "must have 2 elements"
        self._pan_range = sorted(list(value))

    @property
    def tilt(self):
        return self.position[1]

    @tilt.setter
    def tilt(self, value):
        self.position = (None, value)

    @property
    def tilt_range(self):
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
        :return:
        """
        cmd_str = "/Calibration.xml?Action=0" if state is True else "/Calibration.xml?Action=C"
        output = self._read_stream(cmd_str)
        # apparently this was left here?
        print(output)
        return self.get_value_from_xml(output, "Text")

    @property
    def PCCWLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "PCCWLS")

    @property
    def PCWLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "PCWLS")

    @property
    def TDnLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "TDnLS")

    @property
    def TUpLS(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "TUpLS")

    @property
    def battery_voltage(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "BattV")

    @property
    def heater(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "Heater")

    @property
    def temp_f(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "Temp")

    @property
    def list_state(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "ListState")

    @property
    def list_index(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "ListIndex")

    @property
    def control_mode(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "CtrlMode")

    @property
    def auto_patrol(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "AutoPatrol")

    @property
    def dwell(self):
        output = self._read_stream("/CP_Update.xml")
        return self.get_value_from_xml(output, "Dwell")


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
