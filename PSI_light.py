#!/bin/python3
"""
Author:
Petr Ent, Gareth Dunstone
"""

import serial
import random
import operator
from functools import reduce

AVAILABLE_CHANNELS = [0, 1, 3, 4, 5, 6, 7, 8]

def construct_packet(channel: int, value: int, address: int = 1, operation: int = 0, t: int = 1) -> bytearray:
    """
    Constructs a packet according to the protocol laid out by PSI to set a channel to value. G.

    :param channel: channel to set
    :param value: value between 0-1022
    :param address: light address
    :param operation: operation, should be 0 for set.
    :param t: type, apparently should be 0 for us.
    :return:
    """
    if channel not in AVAILABLE_CHANNELS:
        pass
        #raise ValueError("Channel < {} > is not in available channels 0 ,1 ,3 ,4, 5, 6, 7, 8".format(channel))

    if not (0 <= value < 1022):
        raise ValueError("Value < {} > is not within range 0-1022".format(value))
    from functools import reduce


    # header - always ST P.
    packet = [ord('S'), ord('T'), 0, 0, 0, 0, 0]

    # target + nibble of address P.
    packet[2] = (t << 4) | ((address >> 8) & 0x0F)
    # the rest of the address P.
    packet[3] = address & 0xFF

    # opcode + payload P.
    # opcode = 4b channel + 2b instr P.
    # is 'instr' meant to be operation? G.
    # either way this should now be working... G.
    packet[4] = (((channel << 4) & 0xF0) | ((operation << 2) & 0x0C) | ((value >> 8) & 0x03))
    packet[5] = value & 0xFF
    # checksum for last byte of packet.
    chk = 0
    for byt  in packet[:6]:
        chk ^= byt
    packet[6] = chk & 0xFF
    #print(" ".join(["{0:02x}".format(x) for x in packet]))
    return bytearray(packet)


if __name__ == '__main__':
    import time, sys
    ser = serial.Serial('/dev/ttyUSB0', 9600, bytesize=serial.EIGHTBITS,
                        parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE,
                         rtscts=False, dsrdtr=False, xonxoff=False)
    address = 1
    t=1

    def logit(pkt):
        print(*["{:02x}".format(i) for i in pkt])
    while True:
        for c in (0,1,3,4,5,6,7,8):
            activate_packet = construct_packet(c, 1, address=address, operation=2, t=t)
            activate_packet += construct_packet(c, 1, address=address, operation=3, t=t)
            ser.write(activate_packet)
            for v in range(0, 1000):
                intensity_packet = construct_packet(c, v, address=address, operation=0, t=t)
                intensity_packet += construct_packet(c, v, address=address, operation=1, t=t)
                ser.write(intensity_packet)
                time.sleep(0.001)

            intensity_packet = construct_packet(c, 0, address=address, operation=0, t=t)
            intensity_packet += construct_packet(c, 0, address=address, operation=1, t=t)
            ser.write(intensity_packet)
            activate_packet = construct_packet(c, 0, address=address, operation=2, t=t)
            activate_packet += construct_packet(c, 0, address=address, operation=3, t=t)
            ser.write(activate_packet)
    ser.close()
