#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
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
import tempfile
import time
import zipfile
import pygtk
import gtk
import string
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.menuitem import MenuItem
from sugar.graphics.toolcombobox import ToolComboBox
from sugar.graphics.combobox import ComboBox
from sugar.graphics.toggletoolbutton import ToggleToolButton
from sugar.graphics import style
from sugar import profile
from sugar.activity import activity
from sugar import network
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from gettext import gettext as _
import pango
import dbus
import gobject

_TOOLBAR_READ = 2
_TOOLBAR_BOOKS = 3
COLUMN_TITLE = 0
COLUMN_AUTHOR = 1
COLUMN_PATH = 2

_logger = logging.getLogger('get-ia-books-activity')

class BooksToolbar(gtk.Toolbar):
    __gtype_name__ = 'BooksToolbar'

    def __init__(self):
        gtk.Toolbar.__init__(self)
        book_search_item = gtk.ToolItem()

        self._search_entry = gtk.Entry()
        self._search_entry.connect('activate', self._search_entry_activate_cb)
        self._search_entry.connect("key_press_event", self.keypress_cb)

        width = int(gtk.gdk.screen_width() / 2)
        self._search_entry.set_size_request(width, -1)

        book_search_item.add(self._search_entry)
        self._search_entry.show()
        self._search_entry.grab_focus()

        self.insert(book_search_item, -1)
        book_search_item.show()

        self._download = ToolButton('go-down')
        self._download.set_tooltip(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self._get_book_cb)
        self.insert(self._download, -1)
        self._download.show()

    def set_activity(self, activity):
        self.activity = activity

    def _search_entry_activate_cb(self, entry):
        self.activity.find_books(entry.props.text)
        self._hide_results.props.sensitive = True

    def _get_book_cb(self, button):
        self.activity.get_book()
 
    def _enable_button(self,  state):
        self._download.props.sensitive = state
 
    def keypress_cb(self, widget, event):
        keyname = gtk.gdk.keyval_name(event.keyval)
        if keyname == 'Escape':
            self.activity.list_scroller.hide()
            self._hide_results.props.sensitive = False
            return True

class ReadHTTPRequestHandler(network.ChunkedGlibHTTPRequestHandler):
    """HTTP Request Handler for transferring document while collaborating.

    RequestHandler class that integrates with Glib mainloop. It writes
    the specified file to the client in chunks, returning control to the
    mainloop between chunks.

    """
    def translate_path(self, path):
        """Return the filepath to the shared document."""
        return self.server.filepath


class ReadHTTPServer(network.GlibTCPServer):
    """HTTP Server for transferring document while collaborating."""
    def __init__(self, server_address, filepath):
        """Set up the GlibTCPServer with the ReadHTTPRequestHandler.

        filepath -- path to shared document to be served.
        """
        self.filepath = filepath
        network.GlibTCPServer.__init__(self, server_address,
                                       ReadHTTPRequestHandler)


class ReadURLDownloader(network.GlibURLDownloader):
    """URLDownloader that provides content-length and content-type."""

    def get_content_length(self):
        """Return the content-length of the download."""
        if self._info is not None:
            return int(self._info.headers.get('Content-Length'))

    def get_content_type(self):
        """Return the content-type of the download."""
        if self._info is not None:
            return self._info.headers.get('Content-type')
        return None

READ_STREAM_SERVICE = 'read-activity-http'

class GetIABooksActivity(activity.Activity):
    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle)
 
        toolbox = activity.ActivityToolbox(self)
        activity_toolbar = toolbox.get_activity_toolbar()
        activity_toolbar.remove(activity_toolbar.keep)
        activity_toolbar.keep = None
        self.set_toolbox(toolbox)
        
        self._books_toolbar = BooksToolbar()
        toolbox.add_toolbar(_('Books'), self._books_toolbar)
        self._books_toolbar.set_activity(self)
        self._books_toolbar.show()

        toolbox.show()
        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.textview = gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_left_margin(50)
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()
        v_adjustment = self.scrolled.get_vadjustment()
        self.clipboard = gtk.Clipboard(display=gtk.gdk.display_get_default(), selection="CLIPBOARD")
        self.page = 0
        self.textview.grab_focus()

        self.ls = gtk.ListStore(gobject.TYPE_STRING, gobject.TYPE_STRING, gobject.TYPE_STRING)
        tv = gtk.TreeView(self.ls)
        tv.set_rules_hint(True)
        tv.set_search_column(COLUMN_TITLE)
        selection = tv.get_selection()
        selection.set_mode(gtk.SELECTION_SINGLE)
        selection.connect("changed", self.selection_cb)
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn('Title', renderer, text=COLUMN_TITLE)
        col.set_sort_column_id(COLUMN_TITLE)
        tv.append_column(col)
    
        renderer = gtk.CellRendererText()
        col = gtk.TreeViewColumn('Author', renderer, text=COLUMN_AUTHOR)
        col.set_sort_column_id(COLUMN_AUTHOR)
        tv.append_column(col)

        self.list_scroller = gtk.ScrolledWindow(hadjustment=None, vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC, gtk.POLICY_AUTOMATIC)
        self.list_scroller.add(tv)
        
        vbox = gtk.VBox()
        vbox.add(self.scrolled)
        vbox.add(self.list_scroller)
        self.set_canvas(vbox)
        tv.show()
        vbox.show()
        self.list_scroller.show()

        textbuffer = self.textview.get_buffer()
        self.tag = textbuffer.create_tag()
        self.tag.set_property('weight', pango.WEIGHT_BOLD)
        self.tag.set_property( 'foreground', "white")
        self.tag.set_property( 'background', "black")

        # Status of temp file used for write_file:
        self._tempfile = None
        self.toolbox.set_current_toolbar(_TOOLBAR_BOOKS)

    def selection_cb(self, selection):
        tv = selection.get_tree_view()
        model = tv.get_model()
        sel = selection.get_selected()
        if sel:
            model, iter = sel
            self.selected_title = model.get_value(iter,COLUMN_TITLE)
            self.selected_author = model.get_value(iter,COLUMN_AUTHOR)
            self.selected_path = model.get_value(iter,COLUMN_PATH)
            self._books_toolbar._enable_button(True)

    def find_books(self, search_text):
        self._books_toolbar._enable_button(False)
        self.list_scroller.hide()
        self.list_scroller_visible = False
        self.book_selected = False
        self.ls.clear()
        search_tuple = search_text.lower().split()
        if len(search_tuple) == 0:
            self._alert(_('Error'), _('You must enter at least one search word.'))
            self._books_toolbar._search_entry.grab_focus()
            return
        f = open('bookcatalog.txt', 'r')
        while f:
            line = unicode(f.readline(), "iso-8859-1")
            if not line:
                break
            line_lower = line.lower()
            i = 0
            words_found = 0
            while i < len(search_tuple):
                text_index = line_lower.find(search_tuple[i]) 
                if text_index > -1:
                    words_found = words_found + 1
                i = i + 1
            if words_found == len(search_tuple):
                iter = self.ls.append()
                book_tuple = line.split('|')
                self.ls.set(iter, COLUMN_TITLE, book_tuple[0],  COLUMN_AUTHOR, book_tuple[1],  COLUMN_PATH, book_tuple[2].rstrip())
        f.close()
        self.list_scroller.show()
        self.list_scroller_visible = True
     
    def get_book(self):
        self._books_toolbar._enable_button(False)
        if self.selected_path.startswith('PGA'):
            gobject.idle_add(self.download_book,  self.selected_path.replace('PGA', 'http://gutenberg.net.au'),  self._get_book_result_cb)
        elif self.selected_path.startswith('/etext'):
            gobject.idle_add(self.download_book,  "http://www.gutenberg.org/dirs" + self.selected_path + "108.zip",  self._get_old_book_result_cb)
        else:
            gobject.idle_add(self.download_book,  "http://www.gutenberg.org/dirs" + self.selected_path + "-8.zip",  self._get_iso_book_result_cb)
        
    def download_book(self,  url,  result_cb):
        print "get book from",  url
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        getter = ReadURLDownloader(url)
        getter.connect("finished", result_cb)
        getter.connect("progress", self._get_book_progress_cb)
        getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download to %s...", path)
        try:
            getter.start(path)
        except:
            self._alert(_('Error'), _('Connection timed out for ') + self.selected_title)
           
        self._download_content_length = getter.get_content_length()
        self._download_content_type = getter.get_content_type()
        self.textview.grab_focus()
