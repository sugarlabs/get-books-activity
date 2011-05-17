#! /usr/bin/env python

# Copyright (C) 2009 James D. Simmons
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
import time
import gtk

OLD_TOOLBAR = False
try:
    from sugar.graphics.toolbarbox import ToolbarBox
    from sugar.activity.widgets import StopButton
except ImportError:
    OLD_TOOLBAR = True

from sugar.graphics import style
from sugar.graphics.toolbutton import ToolButton
from sugar.graphics.toolcombobox import ToolComboBox
from sugar.graphics.combobox import ComboBox
from sugar.graphics.menuitem import MenuItem
from sugar.graphics import iconentry
from sugar import profile
from sugar.activity import activity
from sugar.bundle.activitybundle import ActivityBundle
from sugar.datastore import datastore
from sugar.graphics.alert import NotifyAlert
from sugar.graphics.alert import Alert
from sugar.graphics.icon import Icon
from gettext import gettext as _
import dbus
import gobject
import ConfigParser
import base64

from listview import ListView
import opds
import languagenames
import devicemanager

_MIMETYPES = {'PDF': u'application/pdf', 'PDF BW': u'application/pdf-bw',
                'EPUB': u'application/epub+zip', 'DJVU': u'image/x.djvu'}
_SOURCES = {}
_SOURCES_CONFIG = {}

_logger = logging.getLogger('get-ia-books-activity')

READ_STREAM_SERVICE = 'read-activity-http'

# directory exists if powerd is running.  create a file here,
# named after our pid, to inhibit suspend.
POWERD_INHIBIT_DIR = '/var/run/powerd-inhibit-suspend'


class GetIABooksActivity(activity.Activity):

    def __init__(self, handle):
        "The entry point to the Activity"
        activity.Activity.__init__(self, handle, False)
        self.max_participants = 1

        self.selected_book = None
        self.queryresults = None
        self._getter = None
        self.show_images = True
        self.languages = {}
        self._lang_code_handler = languagenames.LanguageNames()
        self.catalogs = {}

        if os.path.exists('/etc/get-books.cfg'):
            self._read_configuration('/etc/get-books.cfg')
        else:
            self._read_configuration()

        if OLD_TOOLBAR:
            toolbox = activity.ActivityToolbox(self)
            activity_toolbar = toolbox.get_activity_toolbar()

            self.set_toolbox(toolbox)
            self._books_toolbar = gtk.Toolbar()
            self._add_search_controls(self._books_toolbar)
            self.toolbox.add_toolbar(_('Books'), self._books_toolbar)
            self._books_toolbar.show()
            toolbox.show()
            toolbox.set_current_toolbar(1)
        else:
            toolbar_box = ToolbarBox()
            activity_button = ToolButton()
            color = profile.get_color()
            bundle = ActivityBundle(activity.get_bundle_path())
            icon = Icon(file=bundle.get_icon(), xo_color=color)
            activity_button.set_icon_widget(icon)
            activity_button.show()

            toolbar_box.toolbar.insert(activity_button, 0)
            self._add_search_controls(toolbar_box.toolbar)

            separator = gtk.SeparatorToolItem()
            separator.props.draw = False
            separator.set_expand(True)
            toolbar_box.toolbar.insert(separator, -1)

            toolbar_box.toolbar.insert(StopButton(self), -1)

            self.set_toolbar_box(toolbar_box)
            toolbar_box.show_all()
            self._books_toolbar = toolbar_box.toolbar

        self._create_controls()

        if not self.powerd_running():
            try:
                bus = dbus.SystemBus()
                proxy = bus.get_object('org.freedesktop.ohm',
                               '/org/freedesktop/ohm/Keystore')
                self.ohm_keystore = dbus.Interface(proxy,
                                 'org.freedesktop.ohm.Keystore')
            except dbus.DBusException, e:
                logging.warning("Error setting OHM inhibit: %s" % e)
                self.ohm_keystore = None

    def powerd_running(self):
        self.using_powerd = os.access(POWERD_INHIBIT_DIR, os.W_OK)
        logging.error("using_powerd: %d" % self.using_powerd)
        return self.using_powerd

    def _inhibit_suspend(self):
        if self.using_powerd:
            fd = open(POWERD_INHIBIT_DIR + "/%u" % os.getpid(), 'w')
            logging.error("inhibit_suspend file is %s" % POWERD_INHIBIT_DIR \
                    + "/%u" % os.getpid())
            fd.close()
            return True

        if self.ohm_keystore is not None:
            try:
                self.ohm_keystore.SetKey('suspend.inhibit', 1)
                return self.ohm_keystore.GetKey('suspend.inhibit')
            except dbus.exceptions.DBusException:
                logging.debug("failed to inhibit suspend")
                return False
        else:
            return False

    def _allow_suspend(self):
        if self.using_powerd:
            if os.path.exists(POWERD_INHIBIT_DIR + "/%u" % os.getpid()):
                os.unlink(POWERD_INHIBIT_DIR + "/%u" % os.getpid())
            logging.error("allow_suspend unlinking %s" % POWERD_INHIBIT_DIR \
                    + "/%u" % os.getpid())
            return True

        if self.ohm_keystore is not None:
            try:
                self.ohm_keystore.SetKey('suspend.inhibit', 0)
                return self.ohm_keystore.GetKey('suspend.inhibit')
            except dbus.exceptions.DBusException:
                logging.error("failed to allow suspend")
                return False
        else:
            return False

    def _read_configuration(self, file_name='get-books.cfg'):
        logging.error('Reading configuration from file %s', file_name)
        config = ConfigParser.ConfigParser()
        config.readfp(open(file_name))
        if config.has_option('GetBooks', 'show_images'):
            self.show_images = config.getboolean('GetBooks', 'show_images')
        self.languages = {}
        if config.has_option('GetBooks', 'languages'):
            languages_param = config.get('GetBooks', 'languages')
            for language in languages_param.split(','):
                lang_code = language.strip()
                if len(lang_code) > 0:
                    self.languages[lang_code] = \
                    self._lang_code_handler.get_full_language_name(lang_code)

        for section in config.sections():
            if section != 'GetBooks' and not section.startswith('Catalogs'):
                name = config.get(section, 'name')
                _SOURCES[section] = name
                repo_config = {}
                repo_config['query_uri'] = config.get(section, 'query_uri')
                repo_config['opds_cover'] = config.get(section, 'opds_cover')
                if config.has_option(section, 'summary_field'):
                        repo_config['summary_field'] = config.get(section, 'summary_field')
                else:
                        repo_config['summary_field'] = None
                _SOURCES_CONFIG[section] = repo_config

        logging.error('_SOURCES %s', _SOURCES)
        logging.error('_SOURCES_CONFIG %s', _SOURCES_CONFIG)

        for section in config.sections():
            if section.startswith('Catalogs'):
                catalog_source = section.split('_')[1]
                if not catalog_source in _SOURCES_CONFIG:
                    logging.error('There are not a source for the catalog ' +
                                    'section  %s', section)
                    break
                source_config = _SOURCES_CONFIG[catalog_source]
                opds_cover = source_config['opds_cover']
                for catalog in config.options(section):
                    catalog_config = {}
                    catalog_config['query_uri'] = config.get(section, catalog)
                    catalog_config['opds_cover'] = opds_cover
                    catalog_config['source'] = catalog_source
                    self.catalogs[catalog] = catalog_config

        logging.error('languages %s', self.languages)
        logging.error('catalogs %s', self.catalogs)

    def _add_search_controls(self, toolbar):
        book_search_item = gtk.ToolItem()
        toolbar.search_entry = iconentry.IconEntry()
        toolbar.search_entry.set_icon_from_name(iconentry.ICON_ENTRY_PRIMARY,
                                                'system-search')
        toolbar.search_entry.add_clear_button()
        toolbar.search_entry.connect('activate',
                self.__search_entry_activate_cb)
        width = int(gtk.gdk.screen_width() / 5)
        toolbar.search_entry.set_size_request(width, -1)
        book_search_item.add(toolbar.search_entry)
        toolbar.search_entry.show()
        toolbar.insert(book_search_item, -1)
        book_search_item.show()

        toolbar.source_combo = ComboBox()
        toolbar.source_combo.props.sensitive = True
        toolbar.source_changed_cb_id = \
            toolbar.source_combo.connect('changed', self.__source_changed_cb)
        combotool = ToolComboBox(toolbar.source_combo)
        toolbar.insert(combotool, -1)
        combotool.show()

        if len(self.languages) > 0:
            toolbar.language_combo = ComboBox()
            toolbar.language_combo.props.sensitive = True
            combotool = ToolComboBox(toolbar.language_combo)
            toolbar.language_combo.append_item('all', _('Any language'))
            for key in self.languages.keys():
                toolbar.language_combo.append_item(key, self.languages[key])
            toolbar.language_combo.set_active(0)
            toolbar.insert(combotool, -1)
            combotool.show()
            toolbar.language_changed_cb_id = \
                toolbar.language_combo.connect('changed',
                self.__language_changed_cb)

        if len(self.catalogs) > 0:
            bt_catalogs = ToolButton('catalogs')
            bt_catalogs.set_tooltip(_('Catalogs'))

            toolbar.insert(bt_catalogs, -1)
            bt_catalogs.show()
            palette = bt_catalogs.get_palette()

            for key in self.catalogs.keys():
                menu_item = MenuItem(key)
                menu_item.connect('activate',
                    self.__activate_catalog_cb, self.catalogs[key])
                palette.menu.append(menu_item)
                menu_item.show()
            bt_catalogs.connect('clicked',self.__bt_catalogs_clicked_cb)

        self._device_manager = devicemanager.DeviceManager()
        self._refresh_sources(toolbar)
        self._device_manager.connect('device-added', self.__device_added_cb)
        self._device_manager.connect('device-removed',
                self.__device_removed_cb)

        toolbar.search_entry.grab_focus()
        return toolbar

    def __bt_catalogs_clicked_cb(self, button):
        palette = button.get_palette()
        palette.popup(immediate=True, state=palette.SECONDARY)

    def __activate_catalog_cb(self, menu, catalog_config):
        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.clear()
        logging.error('SOURCE %s', catalog_config['source'])
        self._books_toolbar.search_entry.props.text = ''
        source = catalog_config['source']
        position = _SOURCES_CONFIG[source]['position']
        self._books_toolbar.source_combo.set_active(position)

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        self.queryresults = opds.RemoteQueryResult(catalog_config,
                '', query_language)
        self.show_message(_('Performing lookup, please wait...'))
        self.window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))

        self.queryresults.connect('updated', self.__query_updated_cb)

    def update_format_combo(self, links):
        self.format_combo.handler_block(self.__format_changed_cb_id)
        self.format_combo.remove_all()
        for key in _MIMETYPES.keys():
            if _MIMETYPES[key] in links.keys():
                self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.handler_unblock(self.__format_changed_cb_id)

    def get_search_terms(self):
        return self._books_toolbar.search_entry.props.text

    def __device_added_cb(self, mgr):
        _logger.debug('Device was added')
        self._refresh_sources(self._books_toolbar)

    def __device_removed_cb(self, mgr):
        _logger.debug('Device was removed')
        self._refresh_sources(self._books_toolbar)

    def _refresh_sources(self, toolbar):
        toolbar.source_combo.handler_block(toolbar.source_changed_cb_id)

        #TODO: Do not blindly clear this
        toolbar.source_combo.remove_all()

        position = 0
        for key in _SOURCES.keys():
            toolbar.source_combo.append_item(_SOURCES[key], key)
            _SOURCES_CONFIG[key]['position'] = position
            position = position + 1

        # Add menu for local books
        if len(_SOURCES) > 0:
            toolbar.source_combo.append_separator()
        toolbar.source_combo.append_item('local_books', _('My books'),
                icon_name='activity-journal')

        # TODO: I don't know how work the devices section
        devices = self._device_manager.get_devices()

        if len(devices):
            toolbar.source_combo.append_separator()

        for device in devices:
            dev = device[1]
            mount_point = dev.GetProperty('volume.mount_point')
            label = dev.GetProperty('volume.label')
            if label == '' or label is None:
                capacity = int(dev.GetProperty('volume.partition.media_size'))
                label = (_('%.2f GB Volume') % (capacity / (1024.0 ** 3)))
            _logger.debug('Adding device %s' % (label))
            toolbar.source_combo.append_item(mount_point, label)

        toolbar.source_combo.set_active(0)

        toolbar.source_combo.handler_unblock(toolbar.source_changed_cb_id)

    def __format_changed_cb(self, combo):
        self.show_book_data(False)

    def __language_changed_cb(self, combo):
        self.find_books(self.get_search_terms())

    def __search_entry_activate_cb(self, entry):
        self.find_books(self.get_search_terms())

    def __get_book_cb(self, button):
        self.get_book()

    def enable_button(self,  state):
        self._download.props.sensitive = state
        self.format_combo.props.sensitive = state

    def _create_controls(self):
        self._download_content_length = 0
        self._download_content_type = None

        self.progressbox = gtk.HBox(spacing=20)
        self.progressbar = gtk.ProgressBar()
        self.progressbar.set_orientation(gtk.PROGRESS_LEFT_TO_RIGHT)
        self.progressbar.set_fraction(0.0)
        self.progressbox.pack_start(self.progressbar, expand=True,
                fill=True)
        self.cancel_btn = gtk.Button(stock=gtk.STOCK_CANCEL)
        self.cancel_btn.connect('clicked', self.__cancel_btn_clicked_cb)
        self.progressbox.pack_start(self.cancel_btn, expand=False,
                fill=False)

        self.msg_label = gtk.Label()

        self.listview = ListView(self._lang_code_handler)
        self.listview.connect('selection-changed', self.selection_cb)

        self.list_scroller = gtk.ScrolledWindow(hadjustment=None,
                vadjustment=None)
        self.list_scroller.set_policy(gtk.POLICY_AUTOMATIC,
                gtk.POLICY_AUTOMATIC)
        vadjustment = self.list_scroller.get_vadjustment()
        vadjustment.connect('value-changed',
                self.__vadjustment_value_changed_cb)
        self.list_scroller.add(self.listview)

        self.scrolled = gtk.ScrolledWindow()
        self.scrolled.set_policy(gtk.POLICY_NEVER, gtk.POLICY_AUTOMATIC)
        self.scrolled.props.shadow_type = gtk.SHADOW_NONE
        self.textview = gtk.TextView()
        self.textview.set_editable(False)
        self.textview.set_cursor_visible(False)
        self.textview.set_wrap_mode(gtk.WRAP_WORD)
        self.textview.set_justification(gtk.JUSTIFY_LEFT)
        self.textview.set_left_margin(20)
        self.textview.set_right_margin(20)
        self.scrolled.add(self.textview)
        self.textview.show()
        self.scrolled.show()

        vbox_download = gtk.VBox()

        hbox_format = gtk.HBox()
        format_label = gtk.Label(_('Format:'))
        self.format_combo = ComboBox()
        for key in _MIMETYPES.keys():
            self.format_combo.append_item(_MIMETYPES[key], key)
        self.format_combo.set_active(0)
        self.format_combo.props.sensitive = False
        self.__format_changed_cb_id = \
                self.format_combo.connect('changed', self.__format_changed_cb)

        hbox_format.pack_start(format_label, False, False, 10)
        hbox_format.pack_start(self.format_combo, False, False, 10)
        vbox_download.pack_start(hbox_format, False, False, 10)

        self._download = gtk.Button(_('Get Book'))
        self._download.props.sensitive = False
        self._download.connect('clicked', self.__get_book_cb)
        vbox_download.pack_start(self._download, False, False, 10)

        bottom_hbox = gtk.HBox()

        if self.show_images:
            self.__image_downloader = None
            self.image = gtk.Image()
            self.add_default_image()
            bottom_hbox.pack_start(self.image, False, False, 10)
        bottom_hbox.pack_start(self.scrolled, True, True, 10)
        bottom_hbox.pack_start(vbox_download, False, False, 10)
        bottom_hbox.show_all()

        vbox = gtk.VBox()
        vbox.pack_start(self.msg_label, False, False, 10)
        vbox.pack_start(self.progressbox, False, False, 10)
        vbox.pack_start(self.list_scroller, True, True, 0)
        vbox.pack_start(bottom_hbox, False, False, 10)
        self.set_canvas(vbox)
        self.listview.show()
        vbox.show()
        self.list_scroller.show()
        self.progressbox.hide()
        self.show_message(
                _('Enter words from the Author or Title to begin search.'))

        self._books_toolbar.search_entry.grab_focus()

    def can_close(self):
        self._lang_code_handler.close()
        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None
        return True

    def selection_cb(self, widget):
        # Testing...
        selected_book = self.listview.get_selected_book()
        if self.source == 'local_books':
            if selected_book:
                self.download_url = ''
                self.selected_book = selected_book
                self._download.hide()
                self.show_book_data()
                self._object_id = selected_book.get_object_id()
                self._show_journal_alert(_('Selected book'),
                        self.selected_title)
        else:
            self.clear_downloaded_bytes()
            if selected_book:
                self.update_format_combo(selected_book.get_download_links())
                self.selected_book = selected_book
                self._download.show()
                self.show_book_data()

    def show_message(self, text):
        self.msg_label.set_text(text)
        self.msg_label.show()

    def hide_message(self):
        self.msg_label.hide()

    def show_book_data(self, load_image=True):
        self.selected_title = self.selected_book.get_title()
        book_data = _('Title:\t\t') + self.selected_title + '\n'
        self.selected_author = self.selected_book.get_author()
        book_data += _('Author:\t\t') + self.selected_author + '\n'
        self.selected_publisher = self.selected_book.get_publisher()
        self.selected_summary = self.selected_book.get_summary()
        if (self.selected_summary is not 'Unknown'):
            book_data += _('Summary:\t') + self.selected_summary + '\n'
        self.selected_language_code = self.selected_book.get_language()
        if self.selected_language_code != '':
            try:
                self.selected_language = \
                    self._lang_code_handler.get_full_language_name(
                        self.selected_book.get_language())
            except:
                self.selected_language = self.selected_book.get_language()
            book_data += _('Language:\t') + self.selected_language + '\n'
        book_data += _('Publisher:\t') + self.selected_publisher + '\n'
        if self.source != 'local_books':
            try:
                self.download_url = self.selected_book.get_download_links()[\
                        self.format_combo.props.value]
            except:
                pass
        textbuffer = self.textview.get_buffer()
        textbuffer.set_text('\n' + book_data)
        self.enable_button(True)

        # Cover Image
        self.exist_cover_image = False
        if self.show_images and load_image:
            if self.source == 'local_books':
                cover_image_buffer = self.get_journal_entry_cover_image(
                        self.selected_book.get_object_id())
                if (cover_image_buffer):
                    self.add_image_buffer(
                        self.get_pixbuf_from_buffer(cover_image_buffer))
                else:
                    self.add_default_image()
            else:
                url_image = self.selected_book.get_image_url()
                self.add_default_image()
                if url_image:
                    self.download_image(url_image.values()[0])

    def get_pixbuf_from_buffer(self, image_buffer):
        """Buffer To Pixbuf"""
        pixbuf_loader = gtk.gdk.PixbufLoader()
        pixbuf_loader.write(image_buffer)
        pixbuf_loader.close()
        pixbuf = pixbuf_loader.get_pixbuf()
        return pixbuf

    def get_journal_entry_cover_image(self, object_id):
        ds_object = datastore.get(object_id)
        if 'cover_image' in ds_object.metadata:
            cover_data = ds_object.metadata['cover_image']
            return base64.b64decode(cover_data)
        elif 'preview' in ds_object.metadata:
            return ds_object.metadata['preview']
        else:
            return ""

    def download_image(self,  url):
        self._inhibit_suspend()
        if self.__image_downloader is not None:
            self.__image_downloader.stop_download()
        self.__image_downloader = opds.ImageDownloader(self, url)
        self.__image_downloader.connect('updated', self.__image_updated_cb)

    def __image_updated_cb(self, downloader, file_name):
        if file_name is not None:
            self.add_image(file_name)
            self.exist_cover_image = True
            os.remove(file_name)
        else:
            self.add_default_image()
        self.__image_downloader = None
        self._allow_suspend()

    def add_default_image(self):
        file_path = os.path.join(activity.get_bundle_path(),
                'generic_cover.png')
        self.add_image(file_path)

    def add_image(self, file_path):
        pixbuf = gtk.gdk.pixbuf_new_from_file(file_path)
        self.add_image_buffer(pixbuf)

    def add_image_buffer(self, pixbuf):
        image_height = int(gtk.gdk.screen_height() / 4)
        image_width = image_height / 3 * 2
        width, height = pixbuf.get_width(), pixbuf.get_height()
        scale = 1
        if (width > image_width) or (height > image_height):
            scale_x = image_width / float(width)
            scale_y = image_height / float(height)
            scale = min(scale_x, scale_y)

        pixbuf2 = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, \
                            pixbuf.get_has_alpha(), \
                            pixbuf.get_bits_per_sample(), \
                            image_width, image_height)
        pixbuf2.fill(style.COLOR_PANEL_GREY.get_int())

        margin_x = int((image_width - (width * scale)) / 2)
        margin_y = int((image_height - (height * scale)) / 2)

        pixbuf.scale(pixbuf2, margin_x, margin_y, \
                            image_width - (margin_x * 2), \
                            image_height - (margin_y * 2), \
                            margin_x, margin_y, scale, scale, \
                            gtk.gdk.INTERP_BILINEAR)

        self.image.set_from_pixbuf(pixbuf2)

    def get_query_language(self):
        query_language = None
        if len(self.languages) > 0:
            query_language = self._books_toolbar.language_combo.props.value
        return query_language

    def find_books(self, search_text=''):
        self._inhibit_suspend()
        self.source = self._books_toolbar.source_combo.props.value

        query_language = self.get_query_language()

        self.enable_button(False)
        self.clear_downloaded_bytes()
        self.book_selected = False
        self.listview.clear()

        if self.queryresults is not None:
            self.queryresults.cancel()
            self.queryresults = None

        if self.source == 'local_books':
            self.listview.populate_with_books(
                    self.get_entrys_info(search_text))
        else:
            if search_text is None:
                return
            elif len(search_text) < 3:
                self.show_message(_('You must enter at least 3 letters.'))
                self._books_toolbar.search_entry.grab_focus()
                return
            if self.source == 'Internet Archive':
                self.queryresults = \
                        opds.InternetArchiveQueryResult(search_text,
                        query_language, self)
            elif self.source in _SOURCES_CONFIG:
            #if self.source in _SOURCES_CONFIG:
                repo_configuration = _SOURCES_CONFIG[self.source]
                self.queryresults = opds.RemoteQueryResult(repo_configuration,
                        search_text, query_language)
            else:
                self.queryresults = opds.LocalVolumeQueryResult( \
                            self.source, search_text)

            self.show_message(_('Performing lookup, please wait...'))
            self.window.set_cursor(gtk.gdk.Cursor(gtk.gdk.WATCH))
            self.queryresults.connect('updated', self.__query_updated_cb)

    def __query_updated_cb(self, query, midway):
        logging.debug('__query_updated_cb midway %s', midway)
        self.listview.populate(self.queryresults)
        if len(self.queryresults) == 0:
            self.show_message(_('Sorry, no books could be found.'))
        elif not midway:
            self.hide_message()
            query_language = self.get_query_language()
            logging.error('LANGUAGE %s', query_language)
            if query_language != 'all' and query_language != 'en':
                # the bookserver send english books if there are not books in
                # the requested language
                only_english = True
                for book in self.queryresults.get_book_list():
                    if book.get_language() == query_language:
                        only_english = False
                        break
                if only_english:
                    self.show_message(
                            _('Sorry, we only found english books.'))
        self.window.set_cursor(None)
        self._allow_suspend()

    def __source_changed_cb(self, widget):
        search_terms = self.get_search_terms()
        if search_terms == '':
            self.find_books(None)
        else:
            self.find_books(search_terms)

    def __vadjustment_value_changed_cb(self, vadjustment):

        if not self.queryresults.is_ready():
            return
        try:
            # Use various tricks to update resultset as user scrolls down
            if ((vadjustment.props.upper - vadjustment.props.lower) > 1000 \
                and (vadjustment.props.upper - vadjustment.props.value - \
                vadjustment.props.page_size) / (vadjustment.props.upper - \
                vadjustment.props.lower) < 0.3) or ((vadjustment.props.upper \
                - vadjustment.props.value
                - vadjustment.props.page_size) < 200):
                if self.queryresults.has_next():
                    self.queryresults.update_with_next()
        finally:
            return

    def __cancel_btn_clicked_cb(self, btn):
        if self._getter is not None:
            try:
                self._getter.cancel()
            except:
                _logger.debug('Got an exception while trying' + \
                        'to cancel download')
            self.progressbox.hide()
            self.listview.props.sensitive = True
            self._books_toolbar.search_entry.set_sensitive(True)
            _logger.debug('Download was canceled by the user.')
            self._allow_suspend()

    def get_book(self):
        self.enable_button(False)
        self.progressbox.show_all()
        gobject.idle_add(self.download_book,  self.download_url)

    def download_book(self,  url):
        self._inhibit_suspend()
        logging.error('DOWNLOAD BOOK %s', url)
        self.listview.props.sensitive = False
        self._books_toolbar.search_entry.set_sensitive(False)
        path = os.path.join(self.get_activity_root(), 'instance',
                            'tmp%i' % time.time())
        self._getter = opds.ReadURLDownloader(url)
        self._getter.connect("finished", self._get_book_result_cb)
        self._getter.connect("progress", self._get_book_progress_cb)
        self._getter.connect("error", self._get_book_error_cb)
        _logger.debug("Starting download from %s to %s" % (url,path))
        try:
            self._getter.start(path)
        except:
            self.show_error_alert(_('Error'), _('Connection timed out for ') +
                    self.selected_title)

        self._download_content_length = self._getter.get_content_length()
        self._download_content_type = self._getter.get_content_type()

    def _get_book_result_cb(self, getter, tempfile, suggested_name):
        self.listview.props.sensitive = True
        self._books_toolbar.search_entry.set_sensitive(True)
        if self._download_content_type.startswith('text/html'):
            # got an error page instead
            self._get_book_error_cb(getter, 'HTTP Error')
            return
        self.process_downloaded_book(tempfile,  suggested_name)

    def _get_book_progress_cb(self, getter, bytes_downloaded):
        if self._download_content_length > 0:
            _logger.debug("Downloaded %u of %u bytes...",
                          bytes_downloaded, self._download_content_length)
        else:
            _logger.debug("Downloaded %u bytes...",
                          bytes_downloaded)
        total = self._download_content_length
        self.set_downloaded_bytes(bytes_downloaded,  total)
        while gtk.events_pending():
            gtk.main_iteration()

    def _get_book_error_cb(self, getter, err):
        self.listview.props.sensitive = True
        self.enable_button(True)
        self.progressbox.hide()
        _logger.debug("Error getting document: %s", err)
        self._download_content_length = 0
        self._download_content_type = None
        self._getter = None
        if self.source == 'Internet Archive' and \
           getter._url.endswith('_text.pdf'):
            # in the IA server there are files ending with _text.pdf
            # smaller and better than the .pdf files, but not for all
            # the books. We try to download them and if is not present
            # download the .pdf file
            self.download_url = self.download_url.replace('_text.pdf', '.pdf')
            self.get_book()
        else:
            self._allow_suspend()
            self._show_error_alert(_('Error: Could not download %s. ' +
                    'The path in the catalog seems to be incorrect') %
                    self.selected_title)

    def set_downloaded_bytes(self, downloaded_bytes,  total):
        fraction = float(downloaded_bytes) / float(total)
        self.progressbar.set_fraction(fraction)

    def clear_downloaded_bytes(self):
        self.progressbar.set_fraction(0.0)

    def process_downloaded_book(self,  tempfile,  suggested_name):
        _logger.debug("Got document %s (%s)", tempfile, suggested_name)
        self.create_journal_entry(tempfile)
        self._getter = None
        self._allow_suspend()

    def create_journal_entry(self,  tempfile):
        journal_entry = datastore.create()
        journal_title = self.selected_title
        if self.selected_author != '':
            journal_title = journal_title + ', by ' + self.selected_author
        journal_entry.metadata['title'] = journal_title
        journal_entry.metadata['title_set_by_user'] = '1'
        journal_entry.metadata['keep'] = '0'
        journal_entry.metadata['mime_type'] = \
                self.format_combo.props.value
        # Fix fake mime type for black&white pdfs
        if journal_entry.metadata['mime_type'] == _MIMETYPES['PDF BW']:
            journal_entry.metadata['mime_type'] = _MIMETYPES['PDF']

        journal_entry.metadata['buddies'] = ''
        journal_entry.metadata['icon-color'] = profile.get_color().to_string()
        textbuffer = self.textview.get_buffer()
        journal_entry.metadata['description'] = \
            textbuffer.get_text(textbuffer.get_start_iter(),
                textbuffer.get_end_iter())
        if self.exist_cover_image:
            image_buffer = self._get_preview_image_buffer()
            journal_entry.metadata['preview'] = dbus.ByteArray(image_buffer)
            image_buffer = self._get_cover_image_buffer()
            journal_entry.metadata['cover_image'] = \
                dbus.ByteArray(base64.b64encode(image_buffer))
        else:
            journal_entry.metadata['cover_image'] = ""

        journal_entry.metadata['tags'] = self.source
        journal_entry.metadata['source'] = self.source
        journal_entry.metadata['author'] = self.selected_author
        journal_entry.metadata['publisher'] = self.selected_publisher
        journal_entry.metadata['summary'] = self.selected_summary
        journal_entry.metadata['language'] = self.selected_language_code

        journal_entry.file_path = tempfile
        datastore.write(journal_entry)
        os.remove(tempfile)
        self.progressbox.hide()
        self._object_id = journal_entry.object_id
        self._show_journal_alert(_('Download completed'), self.selected_title)

    def _show_journal_alert(self, title, msg):
        _stop_alert = Alert()
        _stop_alert.props.title = title
        _stop_alert.props.msg = msg
        open_icon = Icon(icon_name='zoom-activity')
        _stop_alert.add_button(gtk.RESPONSE_APPLY,
                                    _('Show in Journal'), open_icon)
        open_icon.show()
        ok_icon = Icon(icon_name='dialog-ok')
        _stop_alert.add_button(gtk.RESPONSE_OK, _('Ok'), ok_icon)
        ok_icon.show()
        # Remove other alerts
        for alert in self._alerts:
            self.remove_alert(alert)

        self.add_alert(_stop_alert)
        _stop_alert.connect('response', self.__stop_response_cb)
        _stop_alert.show()

    def __stop_response_cb(self, alert, response_id):
        if response_id is gtk.RESPONSE_APPLY:
            activity.show_object_in_journal(self._object_id)
        self.remove_alert(alert)

    def _get_preview_image_buffer(self):
        preview_width, preview_height = style.zoom(300), style.zoom(225)

        pixbuf = self.image.get_pixbuf()
        width, height = pixbuf.get_width(), pixbuf.get_height()

        scale = 1
        if (width > preview_width) or (height > preview_height):
            scale_x = preview_width / float(width)
            scale_y = preview_height / float(height)
            scale = min(scale_x, scale_y)

        pixbuf2 = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, \
                            pixbuf.get_has_alpha(), \
                            pixbuf.get_bits_per_sample(), \
                            preview_width, preview_height)
        pixbuf2.fill(style.COLOR_WHITE.get_int())

        margin_x = int((preview_width - (width * scale)) / 2)
        margin_y = int((preview_height - (height * scale)) / 2)

        pixbuf.scale(pixbuf2, margin_x, margin_y, \
                            preview_width - (margin_x * 2), \
                            preview_height - (margin_y * 2), \
                            margin_x, margin_y, scale, scale, \
                            gtk.gdk.INTERP_BILINEAR)
        preview_data = []

        def save_func(buf, data):
            data.append(buf)

        pixbuf2.save_to_callback(save_func, 'png', user_data=preview_data)
        preview_data = ''.join(preview_data)
        return preview_data

    def _get_cover_image_buffer(self):
        pixbuf = self.image.get_pixbuf()
        cover_data = []

        def save_func(buf, data):
            data.append(buf)

        pixbuf.save_to_callback(save_func, 'png', user_data=cover_data)
        cover_data = ''.join(cover_data)
        return cover_data

    def _show_error_alert(self, title, text=None):
        alert = NotifyAlert(timeout=20)
        alert.props.title = title
        alert.props.msg = text
        self.add_alert(alert)
        alert.connect('response', self._alert_cancel_cb)
        alert.show()

    def _alert_cancel_cb(self, alert, response_id):
        self.remove_alert(alert)
        self.textview.grab_focus()

    def get_entrys_info(self, query):
        books = []
        for key in _MIMETYPES.keys():
            books.extend(self.get_entry_info_format(query, _MIMETYPES[key]))
        return books

    def get_entry_info_format(self, query, format):
        books = []
        if query is not None and len(query) > 0:
            ds_objects, num_objects = datastore.find(
                    {'mime_type': '%s' % format,
                    'query': '*%s*' % query})
        else:
            ds_objects, num_objects = datastore.find(
                    {'mime_type': '%s' % format})

        logging.error('Local search %d books found %s format', num_objects,
                    format)
        for i in range(0, num_objects):
            entry = {}
            entry['title'] = ds_objects[i].metadata['title']
            entry['mime'] = ds_objects[i].metadata['mime_type']
            entry['object_id'] = ds_objects[i].object_id

            if 'author' in ds_objects[i].metadata:
                entry['author'] = ds_objects[i].metadata['author']
            else:
                entry['author'] = ''

            if 'publisher' in ds_objects[i].metadata:
                entry['dcterms_publisher'] = \
                    ds_objects[i].metadata['publisher']
            else:
                entry['dcterms_publisher'] = ''

            if 'language' in ds_objects[i].metadata:
                entry['dcterms_language'] = \
                    ds_objects[i].metadata['language']
            else:
                entry['dcterms_language'] = ''

            if 'source' in ds_objects[i].metadata:
                entry['source'] = \
                    ds_objects[i].metadata['source']
            else:
                entry['source'] = ''

            if entry['source'] in _SOURCES_CONFIG:
                repo_configuration = _SOURCES_CONFIG[entry['source']]
                summary_field = repo_configuration['summary_field']
                if 'summary' in ds_objects[i].metadata:
                    entry[summary_field] = ds_objects[i].metadata['summary']
                else:
                    entry[summary_field] = ''
            else:
                repo_configuration = None
            books.append(opds.Book(repo_configuration, entry, ''))
        return books

    def close(self,  skip_save=False):
        "Override the close method so we don't try to create a Journal entry."
        activity.Activity.close(self,  True)

    def save(self):
        pass
