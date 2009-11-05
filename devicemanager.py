#! /usr/bin/env python

# Copyright (C) 2009 Sayamindu Dasgupta <sayamindu@laptop.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA


import os
import logging
import gobject
import dbus

from dbus.mainloop.glib import DBusGMainLoop

_logger = logging.getLogger('get-ia-books-activity')

class DeviceManager(gobject.GObject):
    __gsignals__ = {
        'device-added': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([])),
        'device-removed': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([]))
    }    
    def __init__(self):
        gobject.GObject.__init__(self)

        self._devices = []
        self._bus = dbus.SystemBus ()

        self._populate_devices()

        self._bus.add_signal_receiver(self.__device_added,
				     "DeviceAdded",
                                     "org.freedesktop.Hal.Manager",
                                     "org.freedesktop.Hal",
                                     "/org/freedesktop/Hal/Manager")
        self._bus.add_signal_receiver(self.__device_removed,
				     "DeviceRemoved",
                                     "org.freedesktop.Hal.Manager",
                                     "org.freedesktop.Hal",
                                     "/org/freedesktop/Hal/Manager")
    def _populate_devices(self):
        hal_obj = self._bus.get_object ('org.freedesktop.Hal', '/org/freedesktop/Hal/Manager')
        hal = dbus.Interface (hal_obj, 'org.freedesktop.Hal.Manager')

        udis = hal.FindDeviceByCapability ('volume')
        for udi in udis:
            self.__device_added(udi)

    def _is_removable_volume(self, dev):
        # Apart from determining if this is a removable volume, 
        # this also tries to find if there is a catalog.xml in the 
        # root

        if not dev.QueryCapability('volume'):
            return False

        parent_udi = dev.GetProperty('info.parent')
        parent_dev_obj = self._bus.get_object('org.freedesktop.Hal', parent_udi)
        parent = dbus.Interface(parent_dev_obj, 'org.freedesktop.Hal.Device')

        if not parent.GetProperty('storage.removable'):
            return False

        mount_point = dev.GetProperty('volume.mount_point')

        return os.path.exists(os.path.join(mount_point, 'catalog.xml'))

    def __device_added(self, udi):
        dev_obj = self._bus.get_object ('org.freedesktop.Hal', udi)
        # get an interface to the device
        dev = dbus.Interface (dev_obj, 'org.freedesktop.Hal.Device')
        if self._is_removable_volume(dev):
            self._devices.append((udi, dev))
            self.emit('device-added')
            _logger.debug('DeviceManager: Device was added %s' % str(udi))

    def __device_removed(self, udi):
        for device in self._devices:
            if udi in device:
                self._devices.remove(device)
                self.emit('device-removed')
                _logger.debug('DeviceManager: Device was removed %s' % str(udi))

    def get_devices(self):
        return self._devices

if __name__ == '__main__':
    DBusGMainLoop(set_as_default=True)
    dm = DeviceManager()
    print dm.get_devices()[0][1].GetProperty('volume.mount_point'), dm.get_devices()[0][1].GetProperty('volume.label')

    loop = gobject.MainLoop()
    loop.run()

