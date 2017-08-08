from libs.SysUtil import SysUtil
import paho.mqtt.client as client
import time
from zlib import crc32

def on_message(client, userdata, msg):
    payload = msg.payload.decode("utf-8").strip()
    print("topic: {} payload: {}".format(msg.topic, payload))


def on_connect(client, *args):
    print(args)
    print("Subscribing to rpi/{}/operation".format(SysUtil.get_machineid()))
    client.subscribe("rpi/{}/operation".format(SysUtil.get_machineid()), qos=1)

iden = bytes(SysUtil.get_hostname() + "-Updater", "utf-8")
def setupmqtt():
    mqtt = client.Client(client_id=str(crc32(iden)),
                         clean_session=True,
                         protocol=client.MQTTv311,
                         transport="tcp")

    mqtt.on_connect = on_connect
    mqtt.on_message = on_message
    try:
        with open("mqttpassword") as f:
            mqtt.username_pw_set(username=SysUtil.get_hostname(), password=f.read().strip())
    except:
        print("invalid password")
        mqtt.username_pw_set(username=SysUtil.get_hostname(), password="INVALIDPASSWORD")

    mqtt.connect("10.8.0.1", port=1883)
    mqtt.loop_forever()
    # mqtt.subscribe("rpi/{}/operation".format(SysUtil.get_machineid()), qos=1)


def main():
    setupmqtt()
    # while True:
    #     time.sleep(10)
    #     print("Sleeping")


if __name__ == '__main__':
    main()
