"""GlowShift's window: Screen sync and Studio in one app, sharing one strip.

Screen sync is GlowShift Ambient v3.5.1's window and Studio is Studio v5's, with the
same controls, ranges and defaults, except that screen sync now starts in
Atmospheric mode. Only one of them may use the strip at a time.
"""
import functools
import queue
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from glowshift import settings
from glowshift.animations.designs import Design, load_design, save_design
from glowshift.capture.loop import UPPER_HALF, CaptureOptions, open_screen
from glowshift.capture.loop import WHOLE_SCREEN as STRIP_WHOLE_SCREEN
from glowshift.devices.govee.protocol import SEGMENTS
from glowshift.engine.keyboard import DEFAULT_GRID, LOWER_HALF, MATCH_STRIP, WHOLE_SCREEN, default_client_factory
from glowshift.engine.session import Session, StatusModel
from glowshift.engine.studio import FRAME_RATE, Studio

POLL_MS = 120
HANDOVER_MS = 100
HANDOVER_LIMIT_MS = 10000

# Ambient v3.5.1's defaults, except the mode: Atmospheric, with the same tuning.
SCREEN_SYNC_DEFAULTS = {
    'interval': 5, 'transition': 3, 'brightness': 65,
    'saturation_boost': 1.5, 'minimum_saturation': .60, 'minimum_value': .75,
    'multicolor': True, 'color_mode': 'Atmospheric', 'spatial_mapping': 'Serpentine', 'connect': False,
}
COLOR_MODES = ['Dominant colors', 'Atmospheric', 'Spatial (5x3)']
STRIP_SOURCES = {'Whole screen': STRIP_WHOLE_SCREEN, 'Upper half (sky)': UPPER_HALF}
KEYBOARD_SOURCES = {'Mini screen: whole screen': WHOLE_SCREEN,
                    'Mini screen: lower half (ground)': LOWER_HALF,
                    'Match the strip': MATCH_STRIP}
EFFECTS = ['Moving gradient', 'Rainbow wave', 'Breathing', 'Comet', 'Random sparkle']


def monitor_names(monitors):
    return [f'Monitor {i+1} ({m["width"]} × {m["height"]})' for i, m in enumerate(monitors)]


def bleak_link_factory(address):
    from glowshift.devices.govee.h617a import link_factory
    return link_factory(address)


class App:
    def __init__(self, root, screen_factory=open_screen, link_factory_for=bleak_link_factory,
                 settings_path=None, on_connection_event=None, environ=None,
                 keyboard_client_factory=default_client_factory):
        self.root = root
        self._link_factory_for = link_factory_for
        self._settings_path = settings_path
        self._environ = environ
        self.messages = queue.Queue()
        root.title('GlowShift — Govee H617A')
        root.geometry('900x760')
        root.minsize(680, 420)
        self.address = self._load_address()
        self.session = Session(lambda event: self.messages.put(('sync', event)),
                               self._link, screen_factory, on_connection_event, keyboard_client_factory)
        self.studio = Studio(self._link, lambda event: self.messages.put(('studio', event)), on_connection_event)
        self.status = tk.StringVar()

        top = ttk.Frame(root, padding=(18, 12, 18, 0))
        top.pack(fill='x')
        ttk.Label(top, text='GlowShift', font=('Segoe UI', 20, 'bold')).pack(side='left')
        device = ttk.Frame(top)
        device.pack(side='right')
        self.address_text = tk.StringVar()
        ttk.Label(device, textvariable=self.address_text).pack(side='left', padx=(0, 8))
        self.address_entry = ttk.Entry(device, width=20)
        self.address_entry.pack(side='left')
        ttk.Button(device, text='Save address', command=self.save_address).pack(side='left', padx=(6, 0))
        self._show_address_state()
        ttk.Label(root, textvariable=self.status, wraplength=820, padding=(18, 4)).pack(anchor='w')

        notebook = ttk.Notebook(root)
        notebook.pack(fill='both', expand=True, padx=8, pady=8)
        self.screen_sync = ScreenSyncTab(self, notebook, screen_factory)
        self.studio_tab = StudioTab(self, notebook)
        notebook.add(self.screen_sync.frame, text='Screen sync')
        notebook.add(self.studio_tab.frame, text='Studio')
        root.after(POLL_MS, self.poll)
        root.protocol('WM_DELETE_WINDOW', self.close)

    # The strip address, kept in local settings and never shown in full.
    def _load_address(self):
        try:
            return settings.device_address(self._settings_path, self._environ)
        except settings.InvalidAddress:
            return None

    def _show_address_state(self):
        self.address_text.set('Strip address: saved' if self.address else 'Strip address: not set')

    def save_address(self):
        try:
            settings.save_device_address(self.address_entry.get(), self._settings_path)
        except settings.InvalidAddress as error:
            self.status.set(str(error))
            return
        self.address_entry.delete(0, 'end')
        self.address = self._load_address()
        self._show_address_state()
        self.status.set('Strip address saved on this computer.')

    def _link(self, on_disconnect, cached_services=True):
        return self._link_factory_for(self.address)(on_disconnect=on_disconnect, cached_services=cached_services)

    def require_address(self):
        if self.address:
            return True
        self.status.set('Set the strip address first (top right). It is stored only on this computer.')
        return False

    # One user of the strip at a time.
    def studio_may_use_strip(self):
        if self.session.using_bluetooth:
            self.status.set('Screen sync is using the strip. Stop it before using Studio with the strip.')
            return False
        return self.require_address()

    def poll(self):
        try:
            while True:
                source, event = self.messages.get_nowait()
                if source == 'sync':
                    self.screen_sync.apply(event)
                else:
                    self.studio_tab.apply(event)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self.poll)

    def close(self):
        """Ask both to stop and close the window. Call ``shutdown`` after mainloop."""
        self.session.stop()
        self.studio.disconnect()
        self.root.destroy()

    def shutdown(self, timeout=8.0):
        """Wait for the disconnects, so the strip is released before the process exits."""
        sync = self.session.close(timeout)
        studio = self.studio.close(timeout)
        return sync and studio


class ScreenSyncTab:
    """Ambient v3.5.1's window."""

    def __init__(self, app, parent, screen_factory):
        self.app = app
        self.model = StatusModel()
        self.frame = ttk.Frame(parent)
        defaults = SCREEN_SYNC_DEFAULTS
        self.monitor = tk.StringVar()
        self.interval = tk.DoubleVar(value=defaults['interval'])
        self.transition = tk.DoubleVar(value=defaults['transition'])
        self.brightness = tk.IntVar(value=defaults['brightness'])
        self.saturation_boost = tk.DoubleVar(value=defaults['saturation_boost'])
        self.minimum_saturation = tk.DoubleVar(value=defaults['minimum_saturation'])
        self.minimum_value = tk.DoubleVar(value=defaults['minimum_value'])
        self.multicolor = tk.BooleanVar(value=defaults['multicolor'])
        self.color_mode = tk.StringVar(value=defaults['color_mode'])
        self.spatial_mapping = tk.StringVar(value=defaults['spatial_mapping'])
        self.connect = tk.BooleanVar(value=defaults['connect'])
        self.keyboard = tk.BooleanVar(value=False)
        self.strip_source = tk.StringVar(value='Whole screen')
        self.keyboard_source = tk.StringVar(value='Mini screen: whole screen')
        self.status = tk.StringVar(value=self.model.message)
        self.capture_status = tk.StringVar(value='Capture: idle')
        self.ble_status = tk.StringVar(value='Bluetooth: idle')
        self.keyboard_status = tk.StringVar(value='Keyboard: off')
        self.preview = []
        self._keyboard_cells = {}
        self._shown_grid = None
        self._shown_colors = None
        self._handover_waited = 0

        # Scrollable, as in Ambient: controls are not clipped on small or scaled screens.
        canvas = tk.Canvas(self.frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.frame, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        outer = ttk.Frame(canvas, padding=18)
        window = canvas.create_window((0, 0), window=outer, anchor='nw')
        outer.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event: canvas.itemconfigure(window, width=event.width))
        canvas.bind('<Enter>', lambda event: canvas.bind_all(
            '<MouseWheel>', lambda e: canvas.yview_scroll(-int(e.delta / 120), 'units')))
        canvas.bind('<Leave>', lambda event: canvas.unbind_all('<MouseWheel>'))

        ttk.Label(outer, text='Sample your screen, slowly blend its environmental colors, and optionally '
                              'send them to your H617A.').pack(anchor='w', pady=(0, 14))
        with screen_factory() as screen:
            self.monitors = screen.monitors[1:]
        self.names = monitor_names(self.monitors)
        self.monitor.set(self.names[0] if self.names else '')
        row = ttk.Frame(outer); row.pack(fill='x', pady=5)
        ttk.Label(row, text='Capture screen', width=20).pack(side='left')
        ttk.Combobox(row, textvariable=self.monitor, values=self.names, state='readonly', width=29).pack(side='left')
        for label, var, low, high in [('Sample every (seconds)', self.interval, 1, 15),
                                      ('Transition (seconds)', self.transition, 0, 10),
                                      ('Maximum brightness (%)', self.brightness, 5, 100),
                                      ('Saturation boost (%)', self.saturation_boost, 1, 2.5),
                                      ('Minimum saturation (%)', self.minimum_saturation, 0, 1),
                                      ('Minimum color intensity (%)', self.minimum_value, .15, 1)]:
            row = ttk.Frame(outer); row.pack(fill='x', pady=5)
            ttk.Label(row, text=label, width=24).pack(side='left')
            ttk.Scale(row, from_=low, to=high, variable=var, orient='horizontal', length=290).pack(side='left', padx=6)
            value = ttk.Label(row, width=6)
            value.pack(side='left')

            def update_label(v=var, widget=value, name=label.lower()):
                if 'saturation' in name or 'intensity' in name:
                    widget.config(text=f'{v.get()*100:.0f}%')
                else:
                    widget.config(text=f'{v.get():.0f}{"%" if "brightness" in name else "s"}')
            var.trace_add('write', lambda *args, callback=update_label: callback())
            update_label()
        row = ttk.Frame(outer); row.pack(fill='x', pady=5)
        ttk.Label(row, text='Color analysis mode', width=24).pack(side='left')
        ttk.Combobox(row, textvariable=self.color_mode, values=COLOR_MODES, state='readonly',
                     width=25).pack(side='left', padx=6)
        row = ttk.Frame(outer); row.pack(fill='x', pady=5)
        ttk.Label(row, text='Spatial LED order', width=24).pack(side='left')
        ttk.Combobox(row, textvariable=self.spatial_mapping, values=['Serpentine', 'Row-major'],
                     state='readonly', width=25).pack(side='left', padx=6)
        ttk.Checkbutton(outer, text='Multiple colors (5 columns x 3 rows in Spatial mode)',
                        variable=self.multicolor).pack(anchor='w', pady=(10, 3))
        ttk.Checkbutton(outer, text='Send colors to physical strip (uncheck for preview only)',
                        variable=self.connect).pack(anchor='w', pady=3)
        ttk.Checkbutton(outer, text='Also light the keyboard as a mini screen (needs OpenRGB running '
                                    'with its SDK server on)', variable=self.keyboard).pack(anchor='w', pady=3)
        for label, var, values in [('Strip shows', self.strip_source, list(STRIP_SOURCES)),
                                   ('Keyboard shows', self.keyboard_source, list(KEYBOARD_SOURCES))]:
            row = ttk.Frame(outer); row.pack(fill='x', pady=3)
            ttk.Label(row, text=label, width=24).pack(side='left')
            ttk.Combobox(row, textvariable=var, values=values, state='readonly', width=32).pack(side='left', padx=6)
        ttk.Label(outer, text='Live preview (approximate strip colors)',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(16, 5))
        row = ttk.Frame(outer); row.pack(fill='x')
        for _ in range(SEGMENTS):
            box = tk.Label(row, bg='#2d3750', height=3)
            box.pack(side='left', fill='x', expand=True, padx=1)
            self.preview.append(box)
        ttk.Label(outer, text='Keyboard preview (mini screen)',
                  font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(12, 5))
        self.keyboard_preview = tk.Canvas(outer, height=DEFAULT_GRID[0] * 14, highlightthickness=0, bg='#202533')
        self.keyboard_preview.pack(fill='x')
        row = ttk.Frame(outer); row.pack(fill='x', pady=(17, 10))
        ttk.Button(row, text='Start', command=self.start).pack(side='left', padx=(0, 8))
        ttk.Button(row, text='Stop', command=self.stop).pack(side='left')
        ttk.Label(outer, textvariable=self.status, wraplength=730).pack(anchor='w', pady=5)
        ttk.Label(outer, textvariable=self.capture_status).pack(anchor='w')
        ttk.Label(outer, textvariable=self.ble_status, wraplength=730).pack(anchor='w')
        ttk.Label(outer, textvariable=self.keyboard_status, wraplength=730).pack(anchor='w')
        ttk.Label(outer, wraplength=730, text=(
            'Stop before changing settings. Capture and Bluetooth are independent. Diagnostic records are '
            'kept in %LOCALAPPDATA%\\GlowShift\\logs; screenshots are not saved. Use borderless/windowed '
            'mode if capture is black.')).pack(anchor='w', pady=(8, 0))

    def options(self):
        index = self.names.index(self.monitor.get()) if self.monitor.get() in self.names else 0
        return CaptureOptions(
            monitor=index, interval=float(self.interval.get()), transition=float(self.transition.get()),
            brightness=int(self.brightness.get()), multicolor=self.multicolor.get(),
            saturation_boost=float(self.saturation_boost.get()),
            minimum_saturation=float(self.minimum_saturation.get()),
            minimum_value=float(self.minimum_value.get()), color_mode=self.color_mode.get(),
            spatial_mapping=self.spatial_mapping.get(),
            strip_band=STRIP_SOURCES.get(self.strip_source.get(), STRIP_WHOLE_SCREEN))

    def keyboard_choice(self):
        """What the keyboard should show, or None when it is off."""
        if not self.keyboard.get():
            return None
        return KEYBOARD_SOURCES.get(self.keyboard_source.get(), WHOLE_SCREEN)

    def start(self):
        if not self.monitors:
            self.status.set('No monitors detected.')
            return
        connect = self.connect.get()
        if connect and not self.app.require_address():
            return
        if connect and self.app.studio.active:
            # Studio holds the strip: release it first, then start.
            self.app.studio.disconnect()
            self.status.set('Disconnecting Studio from the strip first...')
            self._handover_waited = 0
            self.app.root.after(HANDOVER_MS, self._start_after_studio)
            return
        self.app.session.start(self.options(), connect, self.keyboard_choice())

    def _start_after_studio(self):
        if self.app.studio.active:
            self._handover_waited += HANDOVER_MS
            if self._handover_waited >= HANDOVER_LIMIT_MS:
                self.status.set('Studio did not release the strip; see diagnostic log.')
                return
            self.app.root.after(HANDOVER_MS, self._start_after_studio)
            return
        self.app.session.start(self.options(), True, self.keyboard_choice())

    def stop(self):
        self.app.session.stop()

    def apply(self, event):
        if not self.model.apply(event):
            return
        self.status.set(self.model.message)
        self.capture_status.set('Capture: ' + self.model.capture)
        self.ble_status.set('Bluetooth: ' + self.model.bluetooth)
        self.keyboard_status.set('Keyboard: ' + self.model.keyboard)
        if self.model.colors is not None and self.model.colors != self._shown_colors:
            self._shown_colors = self.model.colors
            for box, color in zip(self.preview, self.model.colors):
                box.configure(bg='#%02x%02x%02x' % tuple(color))
        if self.model.keyboard_grid is not None and self.model.keyboard_grid != self._shown_grid:
            self._show_keyboard(self.model.keyboard_grid)

    def _show_keyboard(self, grid):
        rows, columns = len(grid), len(grid[0])
        width = max(self.keyboard_preview.winfo_width(), columns * 12)
        if self._shown_grid is None or (len(self._shown_grid), len(self._shown_grid[0])) != (rows, columns):
            self.keyboard_preview.delete('all')
            self._keyboard_cells = {}
            self.keyboard_preview.configure(height=rows * 14)
            size = width / columns
            for row in range(rows):
                for column in range(columns):
                    x = column * size
                    self._keyboard_cells[row, column] = self.keyboard_preview.create_rectangle(
                        x + 1, row * 14 + 1, x + size - 1, row * 14 + 13, width=0)
        for row in range(rows):
            for column in range(columns):
                self.keyboard_preview.itemconfigure(self._keyboard_cells[row, column],
                                                    fill='#%02x%02x%02x' % tuple(grid[row][column]))
        self._shown_grid = grid


class StudioTab:
    """Studio v5's window."""

    def __init__(self, app, parent):
        self.app = app
        self.studio = app.studio
        self.frame = ttk.Frame(parent, padding=18)
        self.colors = ['#ff6030'] * SEGMENTS
        self.selected = '#ff6030'
        self.secondary = '#000000'
        self.brightness = tk.IntVar(value=50)
        self.status = tk.StringVar(value='Disconnected')
        self.live = tk.BooleanVar(value=False)
        self.animation_kind = tk.StringVar(value='Rainbow wave')
        self.animation_speed = tk.DoubleVar(value=1.0)
        self.animation_reverse = tk.BooleanVar(value=False)
        self.buttons = []
        outer = self.frame
        ttk.Label(outer, text='Paint segments, then Apply design to send all 15 colors. Optional live painting '
                              'sends changes immediately.').pack(anchor='w', pady=(0, 18))
        strip = ttk.Frame(outer)
        strip.pack(fill='x')
        for i in range(SEGMENTS):
            button = tk.Button(strip, bg=self.colors[i], width=3, relief='flat', command=functools.partial(self.paint, i))
            button.pack(side='left', fill='x', expand=True, padx=2, ipady=23)
            self.buttons.append(button)
        line = ttk.Frame(outer)
        line.pack(fill='x', pady=(20, 12))
        for name, callback in [('Pick color', self.pick), ('Fill preview', self.fill), ('Send solid color', self.solid),
                               ('Apply design', self.apply_design), ('Save design', self.save),
                               ('Load design', self.load)]:
            ttk.Button(line, text=name, command=callback).pack(side='left', padx=(0, 9))
        ttk.Checkbutton(outer, text='Live painting (send each click to strip)', variable=self.live).pack(anchor='w', pady=(0, 12))
        line = ttk.Frame(outer)
        line.pack(fill='x', pady=(0, 12))
        for name, callback in [('Connect', self.connect), ('Disconnect', self.studio.disconnect),
                               ('Power on', functools.partial(self.power, True)),
                               ('Power off', functools.partial(self.power, False))]:
            ttk.Button(line, text=name, command=callback).pack(side='left', padx=(0, 9))
        line = ttk.Frame(outer)
        line.pack(fill='x', pady=(0, 9))
        ttk.Label(line, text='Brightness (%)').pack(side='left')
        ttk.Scale(line, from_=1, to=100, orient='horizontal', variable=self.brightness).pack(side='left', padx=12, fill='x', expand=True)
        ttk.Label(line, textvariable=self.brightness, width=4).pack(side='left')
        ttk.Button(line, text='Apply brightness', command=self.set_brightness).pack(side='left', padx=12)
        ttk.Separator(outer).pack(fill='x', pady=12)
        ttk.Label(outer, text='Animations', font=('Segoe UI', 13, 'bold')).pack(anchor='w')
        line = ttk.Frame(outer)
        line.pack(fill='x', pady=(8, 6))
        ttk.Label(line, text='Effect').pack(side='left', padx=(0, 6))
        ttk.Combobox(line, textvariable=self.animation_kind, state='readonly', width=19, values=EFFECTS).pack(side='left', padx=(0, 12))
        ttk.Label(line, text='Speed').pack(side='left')
        ttk.Scale(line, from_=0.2, to=3, variable=self.animation_speed, orient='horizontal', length=145).pack(side='left', padx=8)
        ttk.Checkbutton(line, text='Reverse', variable=self.animation_reverse).pack(side='left', padx=8)
        ttk.Button(line, text='Background color', command=self.pick_secondary).pack(side='left', padx=8)
        line = ttk.Frame(outer)
        line.pack(fill='x', pady=(6, 0))
        ttk.Button(line, text='Start animation', command=self.start_animation).pack(side='left', padx=(0, 9))
        ttk.Button(line, text='Stop animation', command=self.studio.stop_animation).pack(side='left')
        ttk.Label(outer, text='Animation uses selected color and optional background color. Some effects may update '
                              'slowly over Bluetooth.').pack(anchor='w', pady=(8, 0))
        ttk.Separator(outer).pack(fill='x', pady=12)
        ttk.Label(outer, text='Status', font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        ttk.Label(outer, textvariable=self.status, wraplength=850).pack(anchor='w', pady=(4, 0))
        ttk.Label(outer, text='Notes: Bluetooth remains connected until Disconnect or closing the app. Close Govee '
                              'Home while using this controller.').pack(anchor='w', pady=(18, 0))

    # Strip actions. Each waits its turn if screen sync is using the strip.
    def connect(self):
        if self.app.studio_may_use_strip():
            self.studio.connect()

    def power(self, on):
        if self.app.studio_may_use_strip():
            self.studio.set_power(on)

    def set_brightness(self):
        if self.app.studio_may_use_strip():
            self.studio.set_brightness(self.brightness.get())

    def apply_design(self):
        if self.app.studio_may_use_strip():
            self.studio.send_colors(self.colors)

    def solid(self):
        if len(set(self.colors)) != 1:
            messagebox.showinfo('Multiple colors', 'Use Apply design for a multicolor pattern, or Fill preview '
                                                   'before Send solid color.')
            return
        self.apply_design()

    def start_animation(self):
        if self.app.studio_may_use_strip():
            self.studio.start_animation({
                'kind': self.animation_kind.get(), 'a': self.selected, 'b': self.secondary,
                'speed': float(self.animation_speed.get()), 'reverse': self.animation_reverse.get(),
                'fps': FRAME_RATE})

    # Painting
    def paint(self, index, send=True):
        self.colors[index] = self.selected
        self.buttons[index].configure(bg=self.selected)
        if send and self.live.get():
            self.apply_design()

    def fill(self):
        for i in range(SEGMENTS):
            self.paint(i, send=False)
        if self.live.get():
            self.apply_design()

    def pick(self):
        _, value = colorchooser.askcolor(color=self.selected)
        if value:
            self.selected = value

    def pick_secondary(self):
        _, value = colorchooser.askcolor(color=self.secondary)
        if value:
            self.secondary = value

    def save(self):
        path = filedialog.asksaveasfilename(defaultextension='.json', filetypes=[('JSON design', '*.json')])
        if path:
            save_design(path, Design(self.colors, self.brightness.get()))

    def load(self):
        path = filedialog.askopenfilename(filetypes=[('JSON design', '*.json')])
        if not path:
            return
        try:
            design = load_design(path)
        except Exception as error:
            messagebox.showerror('Load failed', str(error))
            return
        self.show_design(design)

    def show_design(self, design):
        self.colors = list(design.colors)
        self.brightness.set(design.brightness)
        for button, color in zip(self.buttons, self.colors):
            button.configure(bg=color)

    def apply(self, event):
        if event.kind == 'worker_exit':
            self.status.set('Bluetooth worker exited; see diagnostic log' if event.data['crashed'] else 'Disconnected')
        else:
            self.status.set(event.data['text'])
