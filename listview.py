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

import gobject
import gtk
import pango
import sys
from gettext import gettext as _
import logging

from extListview import ExtListView

_logger = logging.getLogger('get-ia-books-activity')


class ListView(ExtListView):
    __txtRdr = gtk.CellRendererText()
    __txtRdr.props.wrap_mode = pango.WRAP_WORD
    __txtRdr.props.wrap_width = 500
    __txtRdr.props.width = 500
    (ROW_TITLE, ROW_AUTHOR, ROW_PUBLISHER,
    ROW_LANGUAGE, ROW_PUB_DATE, ROW_BOOK) = range(6)

    columns = ((_('Title'), [(__txtRdr, gobject.TYPE_STRING)],
                    (ROW_TITLE,), False, True),
               (_('Author'), [(__txtRdr, gobject.TYPE_STRING)],
                    (ROW_AUTHOR, ROW_TITLE), False,  True),
               (_('Publisher'), [(__txtRdr, gobject.TYPE_STRING)],
                    (ROW_AUTHOR, ROW_TITLE), False,  False),
               (_('Language'), [(__txtRdr, gobject.TYPE_STRING)],
                    (ROW_AUTHOR, ROW_TITLE), False,  False),
               (_('Publish Date'), [(__txtRdr, gobject.TYPE_STRING)],
                    (ROW_AUTHOR, ROW_TITLE), False,  False),
               (None, [(None, gobject.TYPE_PYOBJECT)], (None,), False, False))
    __gsignals__ = {
        'selection-changed': (gobject.SIGNAL_RUN_FIRST,
                          gobject.TYPE_NONE,
                          ([])),
    }

    def __init__(self, lang_code_handler):
        ExtListView.__init__(self, self.columns, sortable=True,
                useMarkup=False, canShowHideColumns=True)
        #self.enableDNDReordering() # Is this needed ?

        self._lang_code_handler = lang_code_handler

        selection = self.get_selection()
        selection.set_mode(gtk.SELECTION_SINGLE)
        selection.connect("changed", self.__selection_changed_cb)

    def __selection_changed_cb(self, selection):
        self.emit('selection-changed')

    def populate(self, results):
        self.populate_with_books(results.get_book_list())

    def populate_with_books(self, books):
        rows = []

        for book in books:
            lang = ''
            try:
                lang = self._lang_code_handler.get_full_language_name(
                                                        book.get_language())
            except:
                pass
            try:
                rows.append([book.get_title(), book.get_author(), \
                    book.get_publisher(), lang, \
                    book.get_published_year(), book])
            except:
                _logger.debug(sys.exc_info())

        self.clear()
        self.insertRows(rows)

    def get_selected_book(self):
        try:
            ret = self.getFirstSelectedRow()[self.ROW_BOOK]
        except IndexError:
            ret = None
        return ret
