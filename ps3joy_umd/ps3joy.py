#!/usr/bin/python
#***********************************************************
#* Software License Agreement (BSD License)
#*
#*  Copyright (c) 2009, Willow Garage, Inc.
#*  All rights reserved.
#*
#*  Redistribution and use in source and binary forms, with or without
#*  modification, are permitted provided that the following conditions
#*  are met:
#*
#*   * Redistributions of source code must retain the above copyright
#*     notice, this list of conditions and the following disclaimer.
#*   * Redistributions in binary form must reproduce the above
#*     copyright notice, this list of conditions and the following
#*     disclaimer in the documentation and/or other materials provided
#*     with the distribution.
#*   * Neither the name of the Willow Garage nor the names of its
#*     contributors may be used to endorse or promote products derived
#*     from this software without specific prior written permission.
#*
#*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
#*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
#*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
#*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
#*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
#*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
#*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
#*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
#*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
#*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
#*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
#*  POSSIBILITY OF SUCH DAMAGE.
#***********************************************************

from bluetooth import *
import select
import fcntl
import os
import time
import sys
import traceback
from array import array
import threading

import roslib; roslib.load_manifest('ps3joy')
import rospy
from std_msgs.msg import Int32

L2CAP_PSM_HIDP_CTRL = 17
L2CAP_PSM_HIDP_INTR = 19

last_ctrl = None

def led_callback(msg):
  global last_ctrl
  print "LED_CALLBACK"
  if last_ctrl is None:
    print "NO CONNECTION for LED"
    return

  setleds = [0x52,
             0x01,
             0x00, 0x00, 0x00, 0x00, 0x00,	# rumble values
             0x00, 0x00, 0x00, 0x00, msg.data,	# 0x10=LED1 .. 0x02=LED4
             0xff, 0x27, 0x10, 0x00, 0x32,	# LED 4
             0xff, 0x27, 0x10, 0x00, 0x32,	# LED 3
             0xff, 0x27, 0x10, 0x00, 0x32,	# LED 2
             0xff, 0x27, 0x10, 0x00, 0x32,	# LED 1
             0x00, 0x00, 0x00, 0x00, 0x00
             ]
  last_ctrl.send(array('B', setleds).tostring())

class uinput:
    EV_KEY = 1
    EV_REL = 2
    EV_ABS = 3
    BUS_USB = 3
    ABS_MAX = 0x3f

class uinputjoy:
    def open_uinput(self):
        for name in ["/dev/input/uinput", "/dev/misc/uinput", "/dev/uinput"]:
            try:
                return os.open(name, os.O_WRONLY)
                break
            except Exception, e:
                #print >> sys.stderr, "Error opening uinput: %s"%str(e)
                pass
        return None

    def __init__(self, buttons, axes, axmin, axmax, axfuzz, axflat):
        self.file = self.open_uinput()
        if self.file == None:
            print >> sys.stderr, "Trying to modprobe uinput."
            os.system("modprobe uinput > /dev/null 2>&1")
            time.sleep(1) # uinput isn't ready to go right away.
            self.file = self.open_uinput()
            if self.file == None:
                print >> sys.stderr, "Can't open uinput device. Is it accessible by this user? Did you mean to run as root?"
                raise IOError
        #id = uinput.input_id()
        #id.bustype = uinput.BUS_USB
        #id.vendor = 0x054C
        #id.product = 0x0268
        #id.version = 0
        #info = uinput.uinput_user_dev()
        #info.name = "Sony Playstation SixAxis/DS3"
        #info.id = id

        UI_SET_EVBIT   = 0x40045564
        UI_SET_KEYBIT  = 0x40045565
        UI_SET_RELBIT  = 0x40045566
        UI_DEV_CREATE  = 0x5501
        UI_SET_RELBIT  = 0x40045566
        UI_SET_ABSBIT  = 0x40045567
        uinput_user_dev = "80sHHHHi" + (uinput.ABS_MAX+1)*4*'i'

        if len(axes) != len(axmin) or len(axes) != len(axmax):
            raise Exception("uinputjoy.__init__: axes, axmin and axmax should have same length")
        absmin = [0] * (uinput.ABS_MAX+1)
        absmax = [0] * (uinput.ABS_MAX+1)
        absfuzz = [2] * (uinput.ABS_MAX+1)
        absflat = [4] * (uinput.ABS_MAX+1)
        for i in range(0, len(axes)):
            absmin[axes[i]] = axmin[i]
            absmax[axes[i]] = axmax[i]
            absfuzz[axes[i]] = axfuzz[i]
            absflat[axes[i]] = axflat[i]

        os.write(self.file, struct.pack(uinput_user_dev, "Sony Playstation SixAxis/DS3",
            uinput.BUS_USB, 0x054C, 0x0268, 0, 0, *(absmax + absmin + absfuzz + absflat)))

        fcntl.ioctl(self.file, UI_SET_EVBIT, uinput.EV_KEY)

        for b in buttons:
            fcntl.ioctl(self.file, UI_SET_KEYBIT, b)

        for a in axes:
            fcntl.ioctl(self.file, UI_SET_EVBIT, uinput.EV_ABS)
            fcntl.ioctl(self.file, UI_SET_ABSBIT, a)

        fcntl.ioctl(self.file, UI_DEV_CREATE)

        self.value = [None] * (len(buttons) + len(axes))
        self.type = [uinput.EV_KEY] * len(buttons) + [uinput.EV_ABS] * len(axes)
        self.code = buttons + axes

        self.last_count = 1

    def update(self, value):
        input_event = "LLHHi"
        t = time.time()
        th = int(t)
        tl = int((t - th) * 1000000)
        if len(value) != len(self.value):
            print >> sys.stderr, "Unexpected length for value in update (%i instead of %i). This is a bug."%(len(value), len(self.value))
        wrote_new = False
        for i in range(0, len(value)):
            if i >= len(self.value) or value[i] != self.value[i]:
                #print "%d: %d" % (i, value[i])
                os.write(self.file, struct.pack(input_event, th, tl, self.type[i], self.code[i], value[i]))
                wrote_new = True
        # Always write something so later drivers don't time out
        if False and not wrote_new:
            self.last_count += 1
            if self.last_count & 127 == 0:
                os.write(self.file, struct.pack(input_event, th, tl, self.type[0], self.code[0], self.last_count))
        self.value = list(value)

class BadJoystickException(Exception):
    def __init__(self):
        Exception.__init__(self, "Unsupported joystick.")

class decoder:
    def __init__(self, inactivity_timeout = float(1e3000)):
        #buttons=[uinput.BTN_SELECT, uinput.BTN_THUMBL, uinput.BTN_THUMBR, uinput.BTN_START,
        #         uinput.BTN_FORWARD, uinput.BTN_RIGHT, uinput.BTN_BACK, uinput.BTN_LEFT,
        #         uinput.BTN_TL, uinput.BTN_TR, uinput.BTN_TL2, uinput.BTN_TR2,
        #         uinput.BTN_X, uinput.BTN_A, uinput.BTN_B, uinput.BTN_Y,
        #         uinput.BTN_MODE]
        #axes=[uinput.ABS_X, uinput.ABS_Y, uinput.ABS_Z, uinput.ABS_RX,
        #         uinput.ABS_RX, uinput.ABS_RY, uinput.ABS_PRESSURE, uinput.ABS_DISTANCE,
        #         uinput.ABS_THROTTLE, uinput.ABS_RUDDER, uinput.ABS_WHEEL, uinput.ABS_GAS,
        #         uinput.ABS_HAT0Y, uinput.ABS_HAT1Y, uinput.ABS_HAT2Y, uinput.ABS_HAT3Y,
        #         uinput.ABS_TILT_X, uinput.ABS_TILT_Y, uinput.ABS_MISC, uinput.ABS_RZ,
        #         ]
        buttons = range(0x100,0x111)
        axes = range(0, 20)
        axmin = [0] * 20
        axmax = [255] * 20
        axfuzz = [2] * 20
        axflat = [4] * 20
        for i in range(-4,0): # Gyros have more bits than other axes
            axmax[i] = 1023
            axfuzz[i] = 4
            axflat[i] = 4
        for i in range(4,len(axmin)-4): # Buttons should be zero when not pressed
            axmin[i] = -axmax[i]
        self.joy = uinputjoy(buttons, axes, axmin, axmax, axfuzz, axflat)
        self.axmid = [sum(pair)/2 for pair in zip(axmin, axmax)]
        self.fullstop() # Probably useless because of uinput startup bug
        self.outlen = len(buttons) + len(axes)
        self.inactivity_timeout = inactivity_timeout
        self.warned_bt1 = False
        self.downed = False

    step_active = 1
    step_idle = 2
    step_error = 3

    def step(self, rawdata): # Returns true if the packet was legal
        if len(rawdata) == 50 or len(rawdata) == 13:
            if len(rawdata) == 13:
                if not self.warned_bt1:
                    print >> sys.stderr, "Your bluetooth adapter is not supported, but it will report button states (on/off) and the 8 main axes."
                    self.warned_bt1 = True
                joy_coding = "!1B2x3B1x4B2x"
            else:
                joy_coding = "!1B2x3B1x4B4x12B15x4H"
            data = list(struct.unpack(joy_coding, rawdata))
            prefix = data.pop(0)
            if prefix != 161:
                print >> sys.stderr, "Unexpected prefix (%i). Is this a PS3 Dual Shock or Six Axis?"%prefix
                return self.step_error
            out = []
            for j in range(0,2): # Split out the buttons.
                curbyte = data.pop(0)
                for k in range(0,8):
                    out.append(int((curbyte & (1 << k)) != 0))
            out = out + data
            self.joy.update(out)
            axis_motion = [abs(out[17:][i] - self.axmid[i]) > 20 for i in range(0,len(out)-17-4)]
                                                                       # 17 buttons, 4 inertial sensors
            if out[16] == 1:
                os.system("hcitool con | grep ACL | awk '{print(\"/usr/bin/hcitool dc\", $3) | \"/bin/bash\" }'")
            if self.downed == False and out[10] == 1 and out[11] == 1 and out[1] == 1 and out[2] == 1:
                # This hasn't been used in a while, and it's kind of dangerous. Replaced with something
                # that requires the user to demonstrate seriousness.
                pass
                #print "SHUTDOWN NOW"
                #os.system("/sbin/shutdown -h now")
                #self.downed = True
            if any(out[0:17]) or any(axis_motion):
                return self.step_active
            return self.step_idle
        else:
            print >> sys.stderr, "Unexpected packet length (%i). Is this a PS3 Dual Shock or Six Axis?"%len(rawdata)
            return self.step_error

    def fullstop(self):
        self.joy.update([0] * 17 + self.axmid)

    def run(self, intr, ctrl):
        activated = False
        try:
            self.fullstop()
            lastactivitytime = lastvalidtime = time.time()
            while True:
                (rd, wr, err) = select.select([intr], [], [], 0.1)
                curtime = time.time()
                if len(rd) + len(wr) + len(err) == 0: # Timeout
                    print "Activating connection."
                    ctrl.send("\x53\xf4\x42\x03\x00\x00") # Try activating the stream.
                    time.sleep(0.5)
                    setleds = [0x52,
                               0x01,
                               0x00, 0x00, 0x00, 0x00, 0x00,        # rumble values
                               0x00, 0x00, 0x00, 0x00, 0x14,        # 0x10=LED1 .. 0x02=LED4
                               0xff, 0x27, 0x10, 0x00, 0x32,        # LED 4
                               0xff, 0x27, 0x10, 0x00, 0x32,        # LED 3
                               0xff, 0x27, 0x10, 0x00, 0x32,        # LED 2
                               0xff, 0x27, 0x10, 0x00, 0x32,        # LED 1
                               0x00, 0x00, 0x00, 0x00, 0x00
                               ]
                    ctrl.send(array('B', setleds).tostring())
                else: # Got a frame.
                    #print "Got a frame at ", curtime, 1 / (curtime - lastvalidtime)
                    if False and not activated:
                        print "Connection activated"
                        activated = True
                        setleds = [0x52,
                                   0x01,
                                   0x00, 0x00, 0x00, 0x00, 0x00,	# rumble values
                                   0x00, 0x00, 0x00, 0x00, 0x14,	# 0x10=LED1 .. 0x02=LED4
                                   0xff, 0x27, 0x10, 0x00, 0x32,	# LED 4
                                   0xff, 0x27, 0x10, 0x00, 0x32,	# LED 3
                                   0xff, 0x27, 0x10, 0x00, 0x32,	# LED 2
                                   0xff, 0x27, 0x10, 0x00, 0x32,	# LED 1
                                   0x00, 0x00, 0x00, 0x00, 0x00
                                   ]
                        ctrl.send(array('B', setleds).tostring())
                    try:
                        rawdata = intr.recv(128)
                    except BluetoothError, s:
                        print "Got Bluetooth error %s. Disconnecting."%s
                        return
                    if len(rawdata) == 0: # Orderly shutdown of socket
                        print "Joystick shut down the connection, battery may be discharged."
                        return
                    stepout = self.step(rawdata)
                    if stepout != self.step_error:
                        lastvalidtime = curtime
                    if stepout == self.step_active:
                        lastactivitytime = curtime
                if curtime - lastactivitytime > self.inactivity_timeout:
                    print "Joystick inactive for %.0f seconds. Disconnecting to save battery."%self.inactivity_timeout
                    return
                if curtime - lastvalidtime >= 0.1: # Zero all outputs if we don't hear a valid frame for 0.1 to 0.2 seconds
                    self.fullstop()
                if curtime - lastvalidtime >= 5: # Disconnect if we don't hear a valid frame for 5 seconds
                    print "No valid data for 5 seconds. Disconnecting. This should not happen, please report it."
                    return
                time.sleep(0.001) # No need to blaze through the loop when there is an error
        finally:
            self.fullstop()

def quit(i):
    os.system("/etc/init.d/bluetooth start > /dev/null 2>&1")
    exit(i)

class connection_manager:
    def __init__(self, decoder):
        self.decoder = decoder
        self.shutdown = False

    def prepare_bluetooth_socket(self, port):
        sock = BluetoothSocket(L2CAP)
        return self.prepare_socket(sock, port)

    def prepare_net_socket(self, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        return self.prepare_socket(sock, port)

    def prepare_socket(self, sock, port):
        try:
            sock.bind(("", port))
        except:
            print >> sys.stderr, "Error binding to socket. Do you have another ps3joy.py running? This error occurs on some distributions (such as Ubuntu Karmic). Please read http://www.ros.org/wiki/ps3joy/Troubleshooting for solutions."
            quit(-1)
        sock.listen(1)
        return sock

    def listen_net(self,intr_port, ctrl_port):
        intr_sock = self.prepare_net_socket(intr_port)
        ctrl_sock = self.prepare_net_socket(ctrl_port)
        self.listen(intr_sock, ctrl_sock)

    def listen_bluetooth(self):
      intr_sock = self.prepare_bluetooth_socket(L2CAP_PSM_HIDP_INTR)
      ctrl_sock = self.prepare_bluetooth_socket(L2CAP_PSM_HIDP_CTRL)
      self.listen(intr_sock, ctrl_sock)

    def listen(self, intr_sock, ctrl_sock):
        global last_ctrl
        self.n = 0
        while not self.shutdown:
            print "Waiting for connection. Disconnect your PS3 joystick from USB and press the pairing button."
            try:
                (intr, (idev, iport)) = intr_sock.accept();
                try:
                    (rd, wr, err) = select.select([ctrl_sock], [], [], 1)
                    if len(rd) == 0:
                        print >> sys.stderr, "Got interrupt connection without control connection. Giving up on it."
                        intr.close()
                        continue
                    (ctrl, (cdev, cport)) = ctrl_sock.accept();
                    try:
                        if idev == cdev:
                            last_ctrl = ctrl
                            self.decoder.run(intr, ctrl)
                            print "Connection terminated."
                        else:
                            print >> sys.stderr, "Simultaneous connection from two different devices. Ignoring both."
                    finally:
                        ctrl.close()
                finally:
                    intr.close()
            except BadJoystickException:
                pass
            except KeyboardInterrupt:
                print "CTRL+C detected. Exiting."
                quit(0)
            except Exception, e:
                traceback.print_exc()
                print >> sys.stderr, "Caught exception: %s"%str(e)
                time.sleep(1)
            print

inactivity_timout_string = "--inactivity-timeout"

def usage():
    print "usage: ps3joy.py ["+inactivity_timout_string+"=n]"
    print "n: inactivity timeout in seconds (saves battery life)."
    sys.exit(0)

def spin():
  print "ROS SPINNING"
  rospy.spin()

if __name__ == "__main__":
    try:
        inactivity_timeout = float(1e3000)
        for arg in sys.argv[1:]: # Be very tolerant in case we are roslaunched.
            if arg == "--help":
                usage()
            elif arg.startswith(inactivity_timout_string):
                if not arg.startswith(inactivity_timout_string+"="):
                    print "Expected '=' after "+inactivity_timout_string
                    print
                    usage()
                str_value = arg[len(inactivity_timout_string)+1:]
                try:
                    inactivity_timeout = float(str_value)
                    if inactivity_timeout < 0:
                        print "Inactivity timeout must be positive."
                        print
                        usage()
                except ValueError:
                    print "Error parsing inactivity timeout: "+str_value
                    print
                    usage()
            else:
                print "Ignoring parameter: '%s'"%arg
        if os.getuid() != 0:
            print >> sys.stderr, "ps3joy.py must be run as root."
            quit(1)
        os.system("/etc/init.d/bluetooth stop > /dev/null 2>&1")
        os.system("hid2hci > /dev/null 2>&1")
        while os.system("hciconfig hci0 > /dev/null 2>&1") != 0:
            print >> sys.stderr,  "No bluetooth dongle found or bluez rosdep not installed. Will retry in 5 seconds."
            time.sleep(5)
        os.system("hciconfig hci0 up > /dev/null 2>&1")
        os.system("hciconfig hci0 pscan > /dev/null 2>&1")
        if inactivity_timeout == float(1e3000):
            print "No inactivity timeout was set. (Run with --help for details.)"
        else:
            print "Inactivity timeout set to %.0f seconds."%inactivity_timeout
        rospy.init_node('ps3joy')
        rospy.Subscriber('/ps3joy/led', Int32, led_callback)
        ros_thread = threading.Thread(target=spin)
        ros_thread.start()
        cm = connection_manager(decoder(inactivity_timeout = inactivity_timeout))
        cm.listen_bluetooth()
    except KeyboardInterrupt:
        print "CTRL+C detected. Exiting."
    quit(0)

