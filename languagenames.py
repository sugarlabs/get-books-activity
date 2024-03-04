#! /usr/bin/env python3

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

from xml.etree import ElementTree
import logging

_ISO_639_XML_PATH = '/usr/share/xml/iso-codes/iso_639.xml'


def singleton(object, instantiated=[]):
    # From http://norvig.com/python-iaq.html
    "Raise an exception if an obj of this class has been instantiated before"
    assert object.__class__ not in instantiated, \
    "%s is a Singleton class but is already instantiated" % object.__class__
    instantiated.append(object.__class__)


class LanguageNames(object):

    def __init__(self):
        singleton(self)
        self._cache = None

    def get_full_language_name(self, code):
        if self._cache == None:
            self._cache = {}
            _xmldoc = ElementTree.parse(_ISO_639_XML_PATH)
            _eroot = _xmldoc.getroot()
            for child in _eroot:
                if child.attrib is not None:
                    lang_name = child.attrib.get('name', None)
                    lang_code = child.attrib.get('iso_639_1_code', None)

                    if lang_code is not None and lang_name is not None:
                        self._cache[lang_code] = lang_name
            _xmldoc = None

        return self._cache[code]