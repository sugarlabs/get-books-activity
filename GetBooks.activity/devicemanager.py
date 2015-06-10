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

from gi.repository import GObject
from gi.repository import Gio


class DeviceManager(GObject.GObject):

    __gsignals__ = {
        'device-changed': (GObject.SignalFlags.RUN_FIRST,
                          None,
                          ([])),
    }

    def __init__(self):
        GObject.GObject.__init__(self)

        self._devices = {}

        self.volume_monitor = Gio.VolumeMonitor.get()
        self.volume_monitor.connect('mount-added', self._mount_added_cb)
        self.volume_monitor.connect('mount-removed', self._mount_removed_cb)

        self._populate_devices()

    def _populate_devices(self):
        for mount in self.volume_monitor.get_mounts():
            props = self._get_props_from_device(mount)
            if mount.can_eject() and props['have_catalog']:
                self._devices[mount] = props

    def _get_props_from_device(self, mount):
        props = {}
        props['removable'] = True
        props['mounted'] = True
        props['mount_path'] = mount.get_default_location().get_path()
        props['label'] = mount.get_name()
        # FIXME: get the proper size here
        props['size'] = 0
        props['have_catalog'] = os.path.exists(\
            os.path.join(props['mount_path'], 'catalog.xml'))
        return props

    def _mount_added_cb(self, volume_monitor, device):
        props = self._get_props_from_device(device)
        self._devices[device] = props
        logging.debug('Device added: %s', props)
        if props['have_catalog']:
            self.emit('device-changed')

    def _mount_removed_cb(self, volume_monitor, device):
        del self._devices[device]
        self.emit('device-changed')

    def get_devices(self):
        return self._devices
