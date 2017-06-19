import datetime
import logging.config
import time
from telnetlib import Telnet
from threading import Thread, Event
from libs.SysUtil import SysUtil
import re
import os
import traceback
from .Light import HelioSpectra

try:
    logging.config.fileConfig("logging.ini")
    logging.getLogger("paramiko").setLevel(logging.WARNING)
except:
    pass

try:
    import telegraf
except Exception as e:
    logging.error("Couldnt import Telegraf, not sending metrics: {}".format(str(e)))


def clamp(v: float, minimum: float, maximum: float) -> float:
    """
    clamps a number to the minimum and maximum.
    
    :param v:
    :param minimum:
    :param maximum:
    :return:
    """
    return min(max(v, minimum), maximum)


TIMEOUT = 10
shell_re = re.compile(b"#")


class ConvironTelNetController(object):
    """
    controller for a telnet device
    """

    def __init__(self, config_section):
        self.ip = \
            self.telnet_username = \
            self.telnet_password = ""
        self.set_command = "pcoset"
        self.get_command = "pcoget"
        self.device_id = 0
        self.cmd_str = "{cmd} {device_id}".format(cmd=self.set_command, device_id=self.device_id)
        self.get_cmd_str = "{cmd} {device_id}".format(cmd=self.get_command, device_id=self.device_id)

        self.init_sequence = ["I 100 26", "I 101 1", "I 102 1"]
        self.teardown_sequence = ["I 123 1", "I 121 1"]
        self.reload_sequence = ["I 100 7", "I 101 1", "I 102 1"]
        self.clear_write = "I 120 0"
        self.clear_busy = "I 123 0"
        self.temp_index = 105
        self.humidity_index = 106
        self.temp_dtype = "I"
        self.humidity_dtype = "I"

        # these all seem to be indexes into the  same data
        self.get_temp_command = "A 1 2"  # get from A row, index 1, count 2
        self.get_humidity_command = "I 4 2"  # get from I row, index 4, count 2
        self.get_par_command = "I 11 1"

        self.logger = logging.getLogger(str(self.__class__))

        # anthing above this line is default
        for k, v in config_section.items():
            setattr(self, k, v)

        self._init_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in self.init_sequence]
        self._teardown_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in
                                   self.teardown_sequence]
        self._reload_sequence = [bytes("{} {}".format(self.cmd_str, i), encoding="UTF") for i in self.reload_sequence]
        self._clear_write = bytes("{} {}".format(self.cmd_str, self.clear_write), encoding="UTF")
        self._clear_busy = bytes("{} {}".format(self.cmd_str, self.clear_busy), encoding="UTF")

        # set temperature and humidity. 105, 106 are the data indices for them.
        self._set_temp = bytes("{} {} {} {{}}".format(self.cmd_str, self.temp_dtype, self.temp_index),
                               encoding="UTF")
        self._set_humidity = bytes("{} {} {} {{}}".format(self.cmd_str, self.humidity_dtype, self.humidity_index),
                                   encoding="UTF")

        # get temp, humidity and par
        self._get_temp = bytes("{} {}".format(self.get_cmd_str, self.get_temp_command), encoding="UTF")
        self._get_humidity = bytes("{} {}".format(self.get_cmd_str, self.get_humidity_command), encoding="UTF")
        self._get_par = bytes("{} {}".format(self.get_cmd_str, self.get_par_command), encoding="UTF")

    def _run(self, telnet, command, expected):
        """Do the leg work between this and the conviron."""
        self.logger.debug("Sending command:  {0!s}".format(command.decode()))
        telnet.write(command + b'\n')
        response = telnet.expect([expected, ], timeout=TIMEOUT)
        self.logger.debug("Received:  {0!s}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Expected response was not received")
        return response

    def _connect_login(self):
        telnet = Telnet(self.ip)
        response = telnet.expect([re.compile(b'login'), ], timeout=TIMEOUT)

        if response[0] < 0:
            raise RuntimeError("Login prompt not recieved")
        self.logger.debug("Intial response is: {0!s}".format(response[2].decode()))
        # we MUST wait a little bit before writing to ensure that the stream isnt being written to.
        time.sleep(0.1)
        payload = bytes(self.telnet_username + "\n", encoding="UTF8")
        telnet.write(payload)

        response = telnet.expect([re.compile(b"Password:"), ], timeout=TIMEOUT)
        self.logger.debug("Sent username: {0!s}".format(payload.decode()))
        self.logger.debug("Received: {0!s}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Password prompt was not received")
        # Password
        payload = bytes(self.telnet_password + "\n", encoding="UTF8")
        telnet.write(payload)

        response = telnet.expect([shell_re, ], timeout=TIMEOUT)
        self.logger.debug("Send password: {0!s}".format(payload.decode()))
        self.logger.debug("Received: {}".format(response[2].decode()))
        if response[0] < 0:  # No match found
            raise RuntimeError("Shell prompt was not received")
        return telnet

    def _clear_write_unflag(self, telnet):
        time.sleep(2)
        self._run(telnet, self._clear_write, shell_re)

    def _clear_busy_unflag(self, telnet):
        time.sleep(2)
        self._run(telnet, self._clear_busy, shell_re)

    def set(self, temperature: int = None, humidity: int = None) -> bool:
        """
        Sets the chamber to a specific temperature and humidity.
        
        Usually to get fine grained control of the temperature, the temperature is multiplied by 10,
        so 19.2 C becomes int(192)
        
        Humidity is usually provided as a percentage.
        
        :param temperature: integer of temperature value 
        :type temperature: int
        :param humidity: integer of humidity value
        :type humidity: int
        :return: bool successful
        """
        telnet = self._connect_login()
        try:

            set_commands = [bytes(self._set_temp.decode().format(int(temperature)), encoding="UTF8"),
                            bytes(self._set_humidity.decode().format(int(humidity)), encoding="UTF8")]
            for cmd in self._init_sequence + set_commands + self._teardown_sequence:
                self._run(telnet, cmd, shell_re)
            self._clear_write_unflag(telnet)
            time.sleep(2)

            for cmd in self._reload_sequence + self._teardown_sequence:
                self._run(telnet, cmd, shell_re)

        except Exception as e:
            self.logger.error("Error running command {}".format(str(e)))
            return False
        finally:
            self._clear_write_unflag(telnet)
            self._clear_busy_unflag(telnet)
            time.sleep(2)
            telnet.write(b'logout\n')
            telnet.close()
        return True

    def get_values(self) -> dict:
        """
        gets humidity, temperature and par from a chamber
        
        :return: dict of values: temp_set, temp_recorded, humidity_set, humidity_recorded, par 
        """
        telnet = self._connect_login()
        values = {}
        try:
            # these always need to need with \r\n because otherwise they will match the chamber name in the
            # telnet response
            par_resp = self._run(telnet, self._get_par, re.compile(rb"\b(\d+)\b \r\n"))
            par_groups = par_resp[1].groups()
            if par_groups[0] is not None:
                values['par'] = float(par_groups[0])
            time.sleep(0.2)

            temp_resp = self._run(telnet, self._get_temp, re.compile(rb"\b(\d+) (\d+)\b \r\n"))
            temp_groups = temp_resp[1].groups()
            if len(temp_groups) <= 1:
                self.logger.error("Less than two values returned for temperature")
            else:
                values['temp_recorded'], values['temp_set'] = map(float, temp_groups[:2])

            time.sleep(0.2)
            hum_resp = self._run(telnet, self._get_humidity, re.compile(rb"\b(\d+) (\d+)\b \r\n"))
            hum_groups = hum_resp[1].groups()
            if len(hum_groups) < 0:
                self.logger.error("Less than two values returned for humidity")
            else:
                values['humidity_recorded'], values['humidity_set'] = map(float, hum_groups[:2])

        except Exception as e:
            self.logger.error("Error setting values {}".format(str(e)))
            raise e
        finally:
            self._clear_busy_unflag(telnet)
            time.sleep(2)
            telnet.write(b'logout\n')
            telnet.close()
        return values

class Chamber(Thread):
    """
    Schedule runner for a chamber.
    """
    accuracy = 150

    def __init__(self, identifier: str, config: dict = None):
        # identifier is NOT OPTIONAL!
        # init with name or not, just extending some of the functionality of Thread
        super().__init__(name=identifier)
        print("Thread started {}: {}".format(self.__class__, identifier))
        # self.communication_queue = queue or deque(tuple(), 256)
        self.logger = logging.getLogger(identifier)
        self.logger.info("Init...")
        self.stopper = Event()
        self.identifier = identifier
        self.config = {}
        try:
            self.config = config.copy()
        except:
            pass

        self.temperature_multiplier = self.config.get("temperature_multiplier", 10.0)

        self.controller = \
            self.csv = \
            self.out_of_range = \
            self.current_timepoint = None
        self.lights = []
        light_configs = self.config.get("lights", [])
        for lc in light_configs:
            try:
                l = HelioSpectra(lc)
                self.lights.append(l)
            except Exception as e:
                self.logger.error("Couldnt add light: {}".format(str(e)))
                traceback.print_exc()

        self.datetimefmt = None
        self._current_temp = float()
        self._current_wavelength_intentisies = list()
        self._current_humidity = float()
        self._current_csv_index = 0

        telnet_config = self.config.get('telnet', {})
        self.data_fp = self.config.get("datafile")

        self.controller = ConvironTelNetController(dict(telnet_config))

        self.current_timepoint = datetime.datetime.now()

    def calculate_current_state(self):
        """
        determines the current state the chamber should be in.
        doesnt send the state.
        sets the internal state of the Light object
        """

        current_timepoint = self.current_timepoint
        last = self.csv[-1][0]
        csv_length = len(self.csv)
        if self.out_of_range:
            current_timepoint = current_timepoint.replace(year=last.year, month=last.month, day=last.day)

        six_hours = 6 * 60 * 60
        two_hours = 2 * 60 * 60
        self.logger.info("Behind by {}".format(current_timepoint - self.csv[self._current_csv_index][0]))
        while current_timepoint >= self.csv[self._current_csv_index][0]:
            print("Behind by {}".format(current_timepoint - self.csv[self._current_csv_index][0]), end="\r")
            # if ((current_timepoint-self.csv[self._current_csv_index][0]).days > 1):
            #     self._current_csv_index += (current_timepoint-self.csv[self._current_csv_index][0]).days*60*60/10
            #     continue
            if ((current_timepoint - self.csv[self._current_csv_index][0]).days > 3):
                self._current_csv_index += 500
                continue
            if ((current_timepoint - self.csv[self._current_csv_index][0]).total_seconds() > six_hours):
                self._current_csv_index += 50
                continue
            if ((current_timepoint - self.csv[self._current_csv_index][0]).total_seconds() > two_hours):
                self._current_csv_index += 10
                continue
            self._current_csv_index += 1

            if self._current_csv_index >= csv_length:
                self.out_of_range = True
                self._current_csv_index = csv_length - 1

                while (last - datetime.timedelta(hours=24)) < self.csv[self._current_csv_index][0]:
                    self._current_csv_index -= 1
                break

        # lights get third to -2nth because the last is simul-dt and second last is total watts
        self._current_wavelength_intentisies = self.csv[self._current_csv_index][3:-2]
        print("Time: {}\nTemp/hum: {}\nIntensities: {}".format(
            self.csv[self._current_csv_index][0].isoformat(),
            self.csv[self._current_csv_index][1:3],
            self.csv[self._current_csv_index][3:-2]))
        try:
            self._current_temp = float(self.csv[self._current_csv_index][1])
        except Exception as e:
            self.logger.error("Error calculating temperature {}".format(str(e)))
            traceback.print_exc()
        try:
            self._current_humidity = self.csv[self._current_csv_index][2]
        except Exception as e:
            self.logger.error("Error calculating humidity {}".format(str(e)))
            traceback.print_exc()

    def stop(self):
        self.stopper.set()

    def run(self):
        """
        runs the chamber continuously.
        """
        self.logger.info("Loading data file {}...".format(self.data_fp))
        print("Loading data file {}...".format(self.data_fp))
        self.csv = SysUtil.load_or_fix_solarcalc(self.data_fp)
        self.current_timepoint = datetime.datetime.now()
        self._current_csv_index = 0
        csv_index = self._current_csv_index
        self.out_of_range = self.current_timepoint > self.csv[-1][0]
        self.logger.info("Loaded {}".format(self.data_fp))
        while True and not self.stopper.is_set():
            self.logger.info("Updating Chamber {}".format(self.name))
            self.current_timepoint = datetime.datetime.now()

            try:
                self.calculate_current_state()
            except Exception:
                print("Couldnt calculate current state.")
                traceback.print_exc()
                self.logger.error(traceback.format_exc())
            if csv_index == self._current_csv_index:
                time.sleep(self.accuracy)
                continue
            csv_index = self._current_csv_index

            chamber_metric = dict()
            chamber_metric['temp_target'] = self._current_temp
            chamber_metric['humidity_target'] = self._current_humidity

            if self.out_of_range:
                self.logger.warning("Running outside of Solarcalc file time range. Repeating the last 24 hours")

            self.logger.info("RUNNING #{0:07d} @ {1} - {2}:{3}".format(self._current_csv_index,
                                                                       self.current_timepoint.isoformat(),
                                                                       self._current_temp, self._current_humidity))
            for _ in range(10):
                try:
                    chamber_metric.update(self.controller.get_values())
                    # collect chamber sensor metrics
                    if type(chamber_metric.get("temp_recorded")) is float:
                        chamber_metric['temp_recorded'] /= self.temperature_multiplier
                    if type(chamber_metric.get("temp_set")) is float:
                        chamber_metric['temp_set'] /= self.temperature_multiplier
                    self.logger.info("Chamber metric: {}".format(str(chamber_metric)))
                    print("Chamber metric: {}".format(str(chamber_metric)))
                    break
                except Exception as e:
                    traceback.print_exc()
                    self.logger.warning("Couldnt collect chamber sensor metric retrying: {}".format(str(e)))
                    print("Failed, retrying ({}/10)".format(_))
            else:
                print("Totally failed getting chamber metrics")
                self.logger.error("Totally failed getting chamber metrics")

            for _ in range(10):
                try:
                    if self.controller.set(temperature=int(self._current_temp * self.temperature_multiplier),
                                           humidity=int(self._current_humidity)):
                        break
                except:
                    traceback.print_exc()
                    self.logger.warning("Couldnt set controller, retrying", traceback.format_exc())
                    print("Failed, retrying ({}/10)".format(_))
            else:
                print("Totally failed setting the chamber.")
                self.logger.error("Totally Failed setting the chamber.")

            light_metrics = list()
            for light in self.lights:
                for _ in range(5):
                    try:
                        metric = light.set(self._current_wavelength_intentisies)
                        light_metrics.append((light.name, metric))
                        break
                    except Exception as e:
                        traceback.print_exc()
                        self.logger.warning(
                            "Error updating lights/collecting light metrics, retrying {}".format(str(e)))
                        print("Failed, retrying ({}/3)".format(_))
                else:
                    print("Totally failed setting lights or getting light metrics")
                    self.logger.error("Totally failed setting lights or getting light metrics")
            if len(light_metrics):
                self.logger.info("light metrics {}".format(str(light_metrics)))
                print("light metrics {}".format(str(light_metrics)))

            try:
                # send metrics.
                telegraf_client = telegraf.TelegrafClient(host="localhost", port=8092)
                if chamber_metric:
                    telegraf_client.metric("conviron", chamber_metric)
                for light_name, lm in light_metrics:
                    telegraf_client.metric("lights", lm, tags={"light_name": light_name})
                self.logger.debug("Communicated chamber and light metrics to telegraf")
            except Exception as exc:
                self.logger.error("Couldn't communicate with telegraf client. {}".format(str(exc)))
            time.sleep(self.accuracy * 2)
