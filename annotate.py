import collections
import colorsys
import itertools
import mariadb
import pylru
import struct
import time
import tkinter
import tkinter.font
import tkinter.simpledialog
import tkinter.ttk

import sys
import traceback
import pprint

class InfoDialog(tkinter.simpledialog.Dialog):
    def __init__(self, title, info, parent = None):
        if not parent:
            raise TypeError

        self.info = info
        self.button = None

        tkinter.simpledialog.Dialog.__init__(self, parent, title)

    def buttonbox(self):
        box = tkinter.Frame(self)

        self.button = tkinter.Button(box, text="OK", width=10, command=self.ok, default=tkinter.ACTIVE)
        self.button.pack(side=tkinter.LEFT, padx=5, pady=5)

        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.ok)

        box.pack()

    def body(self, master):
        w = tkinter.Label(master, text=self.info, justify=tkinter.LEFT)
        w.grid(row=0, padx=5, sticky=tkinter.W)

        return self.button

    def validate(self):
        return 1

class QueryIntegerLiteral(tkinter.simpledialog._QueryDialog):
    errormessage = "Not an integer literal."
    def getresult(self):
        return int(self.entry.get(), 0)

def askintegerliteral(title, prompt, **kw):
    d = QueryIntegerLiteral(title, prompt, **kw)
    return d.result

class TkinterView(object):
    def __init__(self, yscroll=None, **kwargs):
        self.height = 0
        self.items = []
        self.items_len = 0
        self.first_item = 0
        self.item_height = kwargs.get("font", tkinter.font.Font()).metrics("linespace")
        self.page_size = 0

        self.yscroll = yscroll
        self.noyscroll = True

        self.bind("<Configure>", self.resize)
        self.bind("<Expose>", self.draw)
        self.update_geometry()

    def resize(self, event):
        self.height = event.height
        self.page_size = self.height // self.item_height
        self.update_geometry()

    def draw(self, event):
        raise NotImplementedError()

    def item_generate(self):
        raise NotImplementedError()

    def update_geometry(self):
        self.item_generate()

        if self.yscroll and self.page_size:
            start = 0.0
            end = 1.0

            if self.items and self.page_size:
                start = float(self.first_item) / self.items_len
                end = float(self.first_item + (self.page_size // 2)) / self.items_len

            # weird Windows bug
            if self.noyscroll != ((end - start) >= 1.0):
                yscroll = self.yscroll
                self.yscroll = None
                yscroll.destroy()
                self.yscroll = tkinter.Scrollbar(self.master)
                self.yscroll.config(command=self.yview)
                self.yscroll.grid(row=0, column=1, sticky=tkinter.N+tkinter.S)

            self.yscroll.set(start, end)

            self.noyscroll = False
            if end - start >= 1.0:
                self.noyscroll = True

        self.event_generate("<Expose>")

    def setfirst(self, first):
        if first < 0 or self.items_len <= self.page_size:
            first = 0
        elif first >= self.items_len - (self.page_size // 2):
            first = self.items_len - (self.page_size // 2)

        if first != self.first_item:
            self.first_item = first
            self.update_geometry()

    def yview(self, event, value, unit=None):
        if event == "moveto":
            self.setfirst(int(self.items_len * float(value) + 0.5))
        elif event == "scroll":
            if unit == "units":
                self.setfirst(self.first_item + int(value))
            elif unit == "wheel":
                direction = (1, -1)[value < 0]
                while value:
                    self.setfirst(self.first_item + int(direction) * 5)
                    self.update_idletasks()
                    value -= direction
            elif unit == "pages":
                self.setfirst(self.first_item + int(value) * self.page_size)

class DataView(TkinterView, tkinter.Listbox):
    def __init__(self, parent, cursor=None, **kwargs):
        if cursor is None:
            raise TypeError

        self.cursor = cursor
        self.csmap = 0
        self.csaddress = 0
        self.cmap = 0
        self.caddress = 0

        self.map_name = {1:"ROM", 2:"WRAM", 3:"SRAM", 4:"VRAM", 5:"REG"
                        ,"ROM":1, "WRAM":2, "SRAM":3, "VRAM":4, "REG":5}

        self.xscroll = kwargs.pop("xscroll", None)
        self.noxscroll = True
        if self.xscroll is not None:
            kwargs["xscrollcommand"] = self.fixxscrollcommand

        yscroll = kwargs.pop("yscroll", None)
        tkinter.Listbox.__init__(self, parent, **kwargs)

        TkinterView.__init__(self, yscroll, **kwargs)

    def draw(self, event):
        self.delete(0, tkinter.END)
        for item in self.items:
            self.insert(tkinter.END, item)

    def fixxscrollcommand(self, lo, hi):
        start = float(lo)
        end = float(hi)

        # weird Windows bug
        if self.noxscroll != ((end - start) >= 1.0):
            xscroll = self.xscroll
            self.xscroll = None
            xscroll.destroy()
            self.xscroll = tkinter.Scrollbar(self.master, orient=tkinter.HORIZONTAL)
            self.xscroll.config(command=self.xview)
            self.xscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

        self.xscroll.set(lo, hi)

        self.noxscroll = False
        if (end - start) >= 1.0:
            self.noxscroll = True

    def item_generate(self):
        del self.items[:]
        self.items_len = 0

        ptic = time.time()
        count_query = ("SELECT COUNT(*)"
                       "  FROM datamap"
                       " WHERE csmap = ?"
                       "   AND csaddress = ?"
                       "   AND cmap = ?"
                       "   AND caddress = ?")

        tic = time.time()
        self.cursor.execute(count_query, (self.csmap, self.csaddress, self.cmap, self.caddress))
        self.items_len = self.cursor.fetchone()['COUNT(*)']
        toc = time.time()
        if toc - tic > 0.1:
            print("JDB datamap len {}".format(toc - tic))

        if self.items_len == 0:
            return

        first_query = ("SELECT dmap, daddress"
                       "  FROM datamap"
                       " WHERE csmap = ?"
                       "   AND csaddress = ?"
                       "   AND cmap = ?"
                       "   AND caddress = ?"
                       " ORDER BY dmap, daddress, readdata"
                       " LIMIT ?, 1")
        tic = time.time()
        self.cursor.execute(first_query, (self.csmap, self.csaddress, self.cmap, self.caddress, self.first_item))
        first = self.cursor.fetchone()
        toc = time.time()
        if toc - tic > 0.1:
            print("JDB datamap first {}".format(toc - tic))

        page_query = ("SELECT dm.dmap, dm.daddress, dm.readdata, IFNULL(c.comment, IFNULL(d.comment, '')) as comment"
                      "  FROM datamap dm"
                      "  LEFT JOIN functions f ON (f.smap = dm.csmap AND f.saddress = dm.csaddress AND f.map = dm.cmap AND f.begin <= dm.caddress AND dm.caddress <= f.end)"
                      "  LEFT JOIN comments c ON (c.smap = 0 AND c.map = dm.dmap AND c.saddress = 0 AND c.address = dm.daddress AND c.context = f.context)"
                      "  LEFT JOIN comments d ON (d.smap = 0 AND d.map = dm.dmap AND d.saddress = 0 AND d.address = dm.daddress AND d.context = 0)"
                      " WHERE dm.csmap = ?"
                      "   AND dm.csaddress = ?"
                      "   AND dm.cmap = ?"
                      "   AND dm.caddress = ?"
                      "   AND (dm.dmap, dm.daddress) >= (?, ?)"
                      " ORDER BY dm.dmap, dm.daddress, dm.readdata"
                      " LIMIT ?")

        tic = time.time()
        self.cursor.execute(page_query, (self.csmap, self.csaddress, self.cmap, self.caddress
            , first["dmap"], first["daddress"], str(self.page_size)))
        rows = self.cursor.fetchall()
        toc = time.time()
        if toc - tic > 0.1:
            print("JDB datamap data {}".format(toc - tic))

        # For each address...
        for row in rows:
            dmap, readdata, daddress, comment = (row[k] for k in ['dmap', 'readdata', 'daddress', 'comment'])
            self.items.append("{} {} 0x{:06X} - {}".format(self.map_name[dmap], ['w','r'][readdata], daddress, comment))

        ptoc = time.time()
        if ptoc - ptic > 0.2:
            print("JDB data page", ptoc - ptic, self.cmap, self.caddress)

    def setsource(self, csmap, csaddress):
        if csmap != self.csmap or csaddress != self.csaddress:
            self.first_item = 0
            self.csmap = csmap
            self.csaddress = csaddress
            self.xview(0)
            self.update_geometry()

    def setaddress(self, cmap, caddress):
        if cmap != self.cmap or caddress != self.caddress:
            self.first_item = 0
            self.cmap = cmap
            self.caddress = caddress
            self.xview(0)
            self.update_geometry()

class CanvasView(TkinterView, tkinter.Canvas):
    def __init__(self, parent, cursor=None, **kwargs):
        if cursor is None:
            raise TypeError

        self.cursor = cursor
        self.entry_address = None
        self.entry_target = None
        self.menu_address = None
        self.metadata = collections.defaultdict(dict)
        self.max_address = 0

        self.cache = pylru.lrucache(100)
        self.times = collections.defaultdict(dict)

        self.font = kwargs.pop("font", tkinter.font.Font())
        self.spacing = 22
        self.xpos = 0

        self.xscroll = kwargs.pop("xscroll", None)
        self.noxscroll = True

        yscroll = kwargs.pop("yscroll", None)
        tkinter.Canvas.__init__(self, parent, **kwargs)

        TkinterView.__init__(self, yscroll, font=self.font, **kwargs)

        self.xwidth = self.winfo_width()
        self.scan_mark(0, 0)

        self.entryframe = tkinter.Frame(self, height=self.item_height + 4)
        self.entryframe.pack_propagate(False)
        self.entry = tkinter.Entry(self.entryframe, font=self.font)
        self.entry.bind("<Return>", lambda e: self.winfo_toplevel().event_generate("<<CommitEntry>>"))
        self.entry.pack(side=tkinter.LEFT, fill=tkinter.BOTH, expand=True)

        def place_entry(target):
            if self.entry_address:
                self.winfo_toplevel().event_generate("<<CommitEntry>>")
            self.winfo_toplevel().event_generate("<<EntryActive>>")
            self.entry_target = target
            self.entry_address = self.menu_address
            del self.cache[(1, self.entry_address)]
            self.update_geometry()

        def remove_entry(e):
            del self.cache[(1, self.entry_address)]
            self.entry_target = None
            self.entry_address = None
            self.update_geometry()
        self.bind("<<RemoveEntry>>", remove_entry)

        self.menu = tkinter.Menu(self, tearoff=0, font=self.font)
        self.menu.add_command(label="Comment", command=lambda:self.place_entry("Comment"))
        self.menu.add_command(label="Function", command=lambda:self.place_entry("Function"), state=tkinter.DISABLED)
        self.menu.add_separator()
        self.menu.add_command(label="Jump to"
            , command=lambda:self.jump(self.metadata[self.menu_address]["Jump to"][1]), state=tkinter.DISABLED)

        def post_menu(event):
            canvas_xy = (self.canvasx(event.x), self.canvasy(event.y))
            self.menu_address = int(self.gettags(self.find_closest(*canvas_xy))[0])
            state = tkinter.NORMAL if "Function Start" in self.metadata[self.menu_address] else tkinter.DISABLED
            self.menu.entryconfig("Function", state=state)
            state = tkinter.NORMAL if "Jump to" in self.metadata[self.menu_address] else tkinter.DISABLED
            self.menu.entryconfig("Jump to", state=state)
            self.menu.post(event.x_root, event.y_root)
        self.bind("<Button-3>", post_menu)

    def draw(self, event):
        self.delete("all")
        for y, (x, address, text, color, target) in enumerate(self.items):
            if (y * self.item_height) > self.winfo_height():
                break

            x = self.font.measure(' ' * x)
            item = self.create_text(x, y * self.item_height, anchor=tkinter.NW, text=text, font=self.font
                , tags=(str(address)))

            x = self.font.measure(' ' * self.spacing)
            if self.entry_address == address and self.entry_target == target:
                self.entryframe.configure(width=self.xwidth - x - 1)
                self.create_window(x, (y * self.item_height), anchor=tkinter.NW, window=self.entryframe)
                self.entry.focus_set()

            if color >= 0:
                rgb = colorsys.hls_to_rgb(color, 0.5, 0.5)
                fill = "#{:02X}{:02X}{:02X}".format(*[int(c*255) for c in rgb])
                bbox = self.bbox(item)
                bbox = (0, bbox[1], self.xwidth, bbox[3])
                rectangle = self.create_rectangle(bbox, fill=fill, outline=fill, tags=(str(address)))
                self.tag_lower(rectangle)

    def update_geometry(self):
        TkinterView.update_geometry(self)

        window_width = max(self.winfo_reqwidth(), self.winfo_width())
        self.xwidth = max([self.font.measure(' ' * x + text) for (x, address, text, color, target) in self.items]
            + [window_width])

        self.xpos = min(self.xpos, self.xwidth - window_width)

        if self.xscroll:
            self.scan_dragto(-1 * self.xpos, 0, gain=1)

            start = float(self.xpos) / self.xwidth
            end = float(self.xpos + window_width) / self.xwidth

            # weird Windows bug
            if self.noxscroll != ((end - start) >= 1.0):
                xscroll = self.xscroll
                self.xscroll = None
                xscroll.destroy()
                self.xscroll = tkinter.Scrollbar(self.master, orient=tkinter.HORIZONTAL)
                self.xscroll.config(command=self.xview)
                self.xscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

            self.xscroll.set(start, end)

            self.noxscroll = False
            if end - start >= 1.0:
                self.noxscroll = True

    def setxpos(self, xpos):
        if xpos < 0 or self.xwidth <= self.winfo_width():
            xpos = 0
        elif xpos > self.xwidth - self.winfo_width():
            xpos = self.xwidth - self.winfo_width()

        if xpos != self.xpos:
            self.xpos = xpos
            self.update_geometry()

    def xview(self, event, value, unit=None):
        if event == "moveto":
            self.setxpos(int(self.xwidth * float(value) + 0.5))
        elif event == "scroll":
            if unit == "units":
                self.setxpos(self.xpos + (int(value) * 10))
            elif unit == "pages":
                self.setxpos(self.xpos + int(value) * self.winfo_width())

    def jump(self, addr):
        raise NotImplementedError()

class ASMView(CanvasView):
    def __init__(self, parent, cursor=None, **kwargs):
        self.smap = 0
        self.saddress = 0
        self.map_name = {1:"ROM", 2:"WRAM", 3:"SRAM", 4:"VRAM", 5:"REG"
                        ,"ROM":1, "WRAM":2, "SRAM":3, "VRAM":4, "REG":5}
        CanvasView.__init__(self, parent, cursor, **kwargs)
        self.max_address = 0x3fffff
        self.io_address = 0

        def publishaddress(event):
            canvas_xy = (self.canvasx(event.x), self.canvasy(event.y))
            self.io_address = self.gettags(self.find_closest(*canvas_xy))[0]

            # Data I/O comments
            self.winfo_toplevel().event_generate("<<AddressChanged>>")
        self.bind("<ButtonRelease-1>", publishaddress)

    def deMMIO(self, address):
        bank = address >> 16
        page = address & 0xFFFF
        # PPU
        if   (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x2100 <= page <= 0x213f): return (5, page)
        # APU
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x2140 <= page <= 0x217f): return (5, page)
        # CPU
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x2180 <= page <= 0x2183): return (5, page)
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x4016 <= page <= 0x4017): return (5, page)
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x4200 <= page <= 0x421f): return (5, page)
        # DMA
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x4300 <= page <= 0x437f): return (5, page)
        # WRAM
        elif (0x00 <= bank <= 0x3f or 0x80 <= bank <= 0xbf) and (0x0000 <= page <= 0x1fff): return (2, page)
        elif (0x7e <= bank <= 0x7f) and (0x0000 <= page <= 0xffff): return (2, address - 0x7e0000)
        # CART ROM
        elif (0x00 <= bank <= 0x3f) and (0x8000 <= page <= 0xffff): return (1, address)
        elif (0x80 <= bank <= 0xbf) and (0x8000 <= page <= 0xffff): return (1, address - 0x800000)
        elif (0x40 <= bank <= 0x7d) and (0x0000 <= page <= 0xffff): return (1, address - 0x400000)
        elif (0xc0 <= bank <= 0xff) and (0x0000 <= page <= 0xffff): return (1, address - 0xc00000)
        # CART SRAM
        elif (0x20 <= bank <= 0x3f or 0xa0 <= bank <= 0xbf) and (0x6000 <= page <= 0x7fff):
            return (3, (address - 0x6000) & 0xffff)
        return (None, None)

    def AB(pc, m, x, code): return "${:02X}{:02X}".format(code[2], code[1])
    def AIIX(pc, m, x, code): return "(${:02X}{:02X},X)".format(code[2], code[1])
    def AIX(pc, m, x, code): return "${:02X}{:02X},X".format(code[2], code[1])
    def AIY(pc, m, x, code): return "${:02X}{:02X},Y".format(code[2], code[1])
    def AI(pc, m, x, code): return "(${:02X}{:02X})".format(code[2], code[1])
    def AIL(pc, m, x, code): return "[${:02X}{:02X}]".format(code[2], code[1])
    def AL(pc, m, x, code): return "${:02X}{:02X}{:02X}".format(code[3], code[2], code[1])
    def ALIX(pc, m, x, code): return "${:02X}{:02X}{:02X}, X".format(code[3], code[2], code[1])
    def A(pc, m, x, code): return "A"
    def BM(pc, m, x, code): return "${:02X},${:02X}".format(code[2], code[1])
    def DP(pc, m, x, code): return "${:02X}".format(code[1])
    def DPIIX(pc, m, x, code): return "(${:02X},X)".format(code[1])
    def DPIX(pc, m, x, code): return "${:02X},X".format(code[1])
    def DPIY(pc, m, x, code): return "${:02X},Y".format(code[1])
    def DPI(pc, m, x, code): return "(${:02X})".format(code[1])
    def DPIIY(pc, m, x, code): return "(${:02X}),Y".format(code[1])
    def DPIL(pc, m, x, code): return "[${:02X}]".format(code[1])
    def DPILIY(pc, m, x, code): return "[${:02X}],Y".format(code[1])
    def IM(pc, m, x, code): return "#${:02X}".format(code[1])
    def IMM(pc, m, x, code): return ("#${0:02X}{1:02X}", "#${1:02X}")[m].format(code[2], code[1])
    def IMX(pc, m, x, code): return ("#${0:02X}{1:02X}", "#${1:02X}")[x].format(code[2], code[1])
    def I(pc, m, x, code): return ""
    def PCR(pc, m, x, code):
        pc = pc & 0xffff
        offset = struct.unpack("<b", bytearray(code[1:2]))
        return "${:04X}".format((pc + 2 + offset[0]) & 0xFFFF)
    def PCRL(pc, m, x, code):
        pc = pc & 0xffff
        offset = struct.unpack("<h", bytearray(code[1:3]))
        return "${:04X}".format((pc + 3 + offset[0]) & 0xFFFF)
    def SRIIY(pc, m, x, code): return "(${:02X},S),Y".format(code[1])
    def SR(pc, m, x, code): return "${:02X},S".format(code[1])

    decoder = [
          ("BRK", DP) , ("ORA", DPIIX), ("COP", DP)  , ("ORA", SR)   , ("TSB", DP)  , ("ORA", DP)  , ("ASL", DP)  , ("ORA", DPIL)  , ("PHP", I), ("ORA", IMM), ("ASL", A), ("PHD", I), ("TSB", AB)  , ("ORA", AB) , ("ASL", AB) , ("ORA", AL)
        , ("BPL", PCR), ("ORA", DPIIY), ("ORA", DPI) , ("ORA", SRIIY), ("TRB", DP)  , ("ORA", DPIX), ("ASL", DPIX), ("ORA", DPILIY), ("CLC", I), ("ORA", AIY), ("INC", A), ("TCS", I), ("TRB", AB)  , ("ORA", AIX), ("ASL", AIX), ("ORA", ALIX)
        , ("JSR", AB) , ("AND", DPIIX), ("JSL", AL)  , ("AND", SR)   , ("BIT", DP)  , ("AND", DP)  , ("ROL", DP)  , ("AND", DPIL)  , ("PLP", I), ("AND", IMM), ("ROL", A), ("PLD", I), ("BIT", AB)  , ("AND", AB) , ("ROL", AB) , ("AND", AL)
        , ("BMI", PCR), ("AND", DPIIY), ("AND", DPI) , ("AND", SRIIY), ("BIT", DPIX), ("AND", DPIX), ("ROL", DPIX), ("AND", DPILIY), ("SEC", I), ("AND", AIY), ("DEC", A), ("TSC", I), ("BIT", AIX) , ("AND", AIX), ("ROL", AIX), ("AND", ALIX)
        , ("RTI", I)  , ("EOR", DPIIX), ("WDM", DP)  , ("EOR", SR)   , ("MVP", BM)  , ("EOR", DP)  , ("LSR", DP)  , ("EOR", DPIL)  , ("PHA", I), ("EOR", IMM), ("LSR", A), ("PHK", I), ("JMP", AB)  , ("EOR", AB) , ("LSR", AB) , ("EOR", AL)
        , ("BVC", PCR), ("EOR", DPIIY), ("EOR", DPI) , ("EOR", SRIIY), ("MVN", BM)  , ("EOR", DPIX), ("LSR", DPIX), ("EOR", DPILIY), ("CLI", I), ("EOR", AIY), ("PHY", I), ("TCD", I), ("JML", AL)  , ("EOR", AIX), ("LSR", AIX), ("EOR", ALIX)
        , ("RTS", I)  , ("ADC", DPIIX), ("PER", PCRL), ("ADC", SR)   , ("STZ", DP)  , ("ADC", DP)  , ("ROR", DP)  , ("ADC", DPIL)  , ("PLA", I), ("ADC", IMM), ("ROR", A), ("RTL", I), ("JMP", AI)  , ("ADC", AB) , ("ROR", AB) , ("ADC", AL)
        , ("BVS", PCR), ("ADC", DPIIY), ("ADC", DPI) , ("ADC", SRIIY), ("STZ", DPIX), ("ADC", DPIX), ("ROR", DPIX), ("ADC", DPILIY), ("SEI", I), ("ADC", AIY), ("PLY", I), ("TDC", I), ("JMP", AIIX), ("ADC", AIX), ("ROR", AIX), ("ADC", ALIX)
        , ("BRA", PCR), ("STA", DPIIX), ("BRL", PCRL), ("STA", SR)   , ("STY", DP)  , ("STA", DP)  , ("STX", DP)  , ("STA", DPIL)  , ("DEY", I), ("BIT", IMM), ("TXA", I), ("PHB", I), ("STY", AB)  , ("STA", AB) , ("STX", AB) , ("STA", AL)
        , ("BCC", PCR), ("STA", DPIIY), ("STA", DPI) , ("STA", SRIIY), ("STY", DPIX), ("STA", DPIX), ("STX", DPIY), ("STA", DPILIY), ("TYA", I), ("STA", AIY), ("TXS", I), ("TXY", I), ("STZ", AB)  , ("STA", AIX), ("STZ", AIX), ("STA", ALIX)
        , ("LDY", IMX), ("LDA", DPIIX), ("LDX", IMX) , ("LDA", SR)   , ("LDY", DP)  , ("LDA", DP)  , ("LDX", DP)  , ("LDA", DPIL)  , ("TAY", I), ("LDA", IMM), ("TAX", I), ("PLB", I), ("LDY", AB)  , ("LDA", AB) , ("LDX", AB) , ("LDA", AL)
        , ("BCS", PCR), ("LDA", DPIIY), ("LDA", DPI) , ("LDA", SRIIY), ("LDY", DPIX), ("LDA", DPIX), ("LDX", DPIY), ("LDA", DPILIY), ("CLV", I), ("LDA", AIY), ("TSX", I), ("TYX", I), ("LDY", AIX) , ("LDA", AIX), ("LDX", AIY), ("LDA", ALIX)
        , ("CPY", IMX), ("CMP", DPIIX), ("REP", IM)  , ("CMP", SR)   , ("CPY", DP)  , ("CMP", DP)  , ("DEC", DP)  , ("CMP", DPIL)  , ("INY", I), ("CMP", IMM), ("DEX", I), ("WAI", I), ("CPY", AB)  , ("CMP", AB) , ("DEC", AB) , ("CMP", AL)
        , ("BNE", PCR), ("CMP", DPIIY), ("CMP", DPI) , ("CMP", SRIIY), ("PEI", DPI) , ("CMP", DPIX), ("DEC", DPIX), ("CMP", DPILIY), ("CLD", I), ("CMP", AIY), ("PHX", I), ("STP", I), ("JML", AIL) , ("CMP", AIX), ("DEC", AIX), ("CMP", ALIX)
        , ("CPX", IMX), ("SBC", DPIIX), ("SEP", IM)  , ("SBC", SR)   , ("CPX", DP)  , ("SBC", DP)  , ("INC", DP)  , ("SBC", DPIL)  , ("INX", I), ("SBC", IMM), ("NOP", I), ("XBA", I), ("CPX", AB)  , ("SBC", AB) , ("INC", AB) , ("SBC", AL)
        , ("BEQ", PCR), ("SBC", DPIIY), ("SBC", DPI) , ("SBC", SRIIY), ("PEA", AB)  , ("SBC", DPIX), ("INC", DPIX), ("SBC", DPILIY), ("SED", I), ("SBC", AIY), ("PLX", I), ("XCE", I), ("JSR", AIIX), ("SBC", AIX), ("INC", AIX), ("SBC", ALIX)
        ]

    def item_generate(self):
        self.times.clear()
        del self.items[:]
        self.items_len = 0
        self.metadata.clear()

        ptic = time.time()
        count_query = ("SELECT COUNT(*)"
                       "  FROM (SELECT 1"
                       "          FROM codemap cm"
                       "         WHERE cm.smap = %(smap)s"
                       "           AND cm.saddress = %(saddress)s"
                       " UNION ALL"
                       "        SELECT 1"
                       "          FROM comments c"
                       "         WHERE c.length IS NOT NULL"
                       "           AND c.smap = %(smap)s"
                       "           AND c.saddress = %(saddress)s"
                       "       ) u")

        tic = time.time()
        self.cursor.execute(count_query, {"smap":self.smap, "saddress":self.saddress})
        self.items_len = self.cursor.fetchone()['COUNT(*)']
        toc = time.time()
        self.times["count"][-1] = toc - tic

        if self.items_len == 0:
            return

        first_query = (" SELECT cm.map, cm.address"
                       "   FROM codemap cm"
                       "  WHERE cm.smap = %(smap)s"
                       "    AND cm.saddress = %(saddress)s"
                       " UNION ALL"
                       " SELECT c.map, c.address"
                       "   FROM comments c"
                       "  WHERE c.length IS NOT NULL"
                       "    AND c.smap = %(smap)s"
                       "    AND c.saddress = %(saddress)s"
                       " ORDER BY map, address"
                       " LIMIT %(first_item)s, 1")
        tic = time.time()
        self.cursor.execute(first_query, {"smap":self.smap, "saddress":self.saddress, "first_item":self.first_item})
        self.first = self.cursor.fetchone()
        toc = time.time()
        self.times["first"][-1] = toc - tic

        page_query = (" SELECT 'code' as asmtype, cm.map, cm.address, cm.m, cm.x, NULL as length"
                      "   FROM codemap cm"
                      "  WHERE cm.smap = %(smap)s"
                      "    AND cm.saddress = %(saddress)s"
                      "    AND cm.map >= %(map)s"
                      "    AND cm.address >= %(address)s"
                      "  GROUP BY address"
                      " UNION ALL"
                      " SELECT 'data' as asmtype, c.map, c.address, NULL as m, NULL as x, c.length"
                      "   FROM comments c"
                      "  WHERE c.length IS NOT NULL"
                      "    AND c.smap = %(smap)s"
                      "    AND c.saddress = %(saddress)s"
                      "    AND c.map = %(map)s"
                      "    AND c.address >= %(address)s"
                      " ORDER BY address"
                      " LIMIT %(page_size)s")
        tic = time.time()
        self.cursor.execute(page_query, {"smap":self.smap, "saddress":self.saddress, "map":self.first["map"], "address":self.first["address"], "page_size":self.page_size})
        rows = self.cursor.fetchall()
        toc = time.time()
        self.times["page"][-1] = toc - tic

        if not rows:
            return

        function_query = ("SELECT f.map, f.begin, f.end, f.name, f.context, f.row_num / c.cnt AS color"
                          "  FROM (SELECT *, ROW_NUMBER() OVER (ORDER BY begin) row_num FROM functions) f"
                          "     , (SELECT COUNT(*) AS cnt FROM functions) c"
                          " WHERE f.smap = ?"
                          "   AND f.saddress = ?"
                          "   AND f.map = ?"
                          "   AND f.begin <= ?"
                          "   AND f.end >= ?")
        tic = time.time()
        self.cursor.execute(function_query, (self.smap, self.saddress, rows[0]["map"], rows[-1]["address"], rows[0]["address"]))
        toc = time.time()
        self.times["function 1"][self.first["address"]] = toc - tic
        functions = self.cursor.fetchall()

        comment_query = ("SELECT address, context, comment"
                         "  FROM comments"
                         " WHERE smap = ?"
                         "   AND saddress = ?"
                         "   AND map = ?"
                         "   AND address >= ?"
                         "   AND address <= ?"
                         " ORDER BY context DESC")
        tic = time.time()
        self.cursor.execute(comment_query, (self.smap, self.saddress, rows[0]["map"], rows[0]["address"], rows[-1]["address"]))
        toc = time.time()
        self.times["comment"][self.first["address"]] = toc - tic
        comments = self.cursor.fetchall()

        # For each address...
        for row in rows:
            asmtype, map, address, m, x, length = (row[k] for k in ['asmtype', 'map', 'address', 'm', 'x', 'length'])

            if len(self.items) > self.page_size:
                break

            if (map, address) in self.cache:
                line, meta = self.cache[(map, address)]
                if not self.items or line[0] != self.items[-1]:
                    self.items += line
                    self.metadata[address] = meta
                continue

            # TODO WRAM and context
            bytes_query = ("SELECT byte"
                           "  FROM bytes"
                           " WHERE smap = %(smap)s"
                           "   AND saddress = %(saddress)s"
                           "   AND map = %(map)s"
                           "   AND address >= %(address)s + 0"
                           "   AND address <= %(address)s + %(length)s"
                           " ORDER BY address")
            function_query = ("SELECT name, context"
                              "  FROM functions"
                              " WHERE smap = ?"
                              "   AND saddress = ?"
                              "   AND map = ?"
                              "   AND begin = ?")
            call_query = ("SELECT f.map, f.begin, f.name"
                          "  FROM calls c"
                          "  LEFT JOIN functions f ON (c.fsmap = f.smap AND c.fsaddress = f.saddress"
                          "                       AND c.fmap = f.map AND c.faddress = f.begin)"
                          " WHERE c.smap = ?"
                          "   AND c.saddress = ?"
                          "   AND c.map = ?"
                          "   AND c.address = ?")
            data_query = (" WITH ranked_messages AS ("
                          "   SELECT c.context, dm.dmap, c.comment, ROW_NUMBER() OVER (PARTITION BY dm.dmap ORDER BY c.context DESC, dm.daddress) AS rn"
                          "     FROM (SELECT DISTINCT dmap, daddress"
                          "             FROM datamap"
                          "            WHERE csmap = ? AND csaddress = ? AND cmap = ? AND caddress = ?"
                          "            GROUP BY dmap) dm"
                          "     LEFT JOIN comments c ON (c.map = dm.dmap AND c.address = dm.daddress AND c.context IN (0, ?))"
                          " )"
                          " SELECT context, dmap, IFNULL(comment, '') as comment FROM ranked_messages WHERE rn = 1;")

            line = []

            # Colorize
            function = [f for f in functions if f['begin'] <= row['address'] and row['address'] <= f['end']]
            if function:
                color = float(function[0]['color']) * 16
                self.metadata[address]["Function"] = function[0]['name']
                self.metadata[address]["Context"] = function[0]['context']
            else:
                color = -1.0

            # Retrieve bytes
            tic = time.time()
            self.cursor.execute(bytes_query, {"smap":self.smap, "saddress":self.saddress, "map":map, "address":address, "length":3})
            toc = time.time()
            self.times["bytes"][address] = toc - tic
            code = [sublist['byte'] for sublist in self.cursor]

            # Function start
            if asmtype == "code":
                if function and map == function[0]['map'] and address == function[0]['begin']:
                    line.append([self.spacing, address, function[0]['name'] + "()", color, "Function"])
                    self.metadata[address]["Function Start"] = True
                    if self.entry_address == address and self.entry_target == "Function":
                        self.entry.delete(0, tkinter.END)
                        self.entry.insert(0, function[0]['name'])

            # Explicit function call
            if asmtype == "code":
                fmap = None
                faddress = None
                if code[0] == 0x20:
                    faddress = (address & 0xFF0000) + struct.unpack("<H", bytearray(code[1:3]))[0]
                    fmap = map
                elif code[0] == 0x22:
                    faddress = struct.unpack("<I", bytearray(code[1:4] + [0]))[0]
                    (fmap, faddress) = self.deMMIO(faddress)
                if faddress:
                    tic = time.time()
                    self.cursor.execute(function_query, (self.smap, self.saddress, fmap, faddress))
                    toc = time.time()
                    self.times["function 2"][address] = toc - tic
                    call = self.cursor.fetchone()
                    if call:
                        line.append([self.spacing, address, "Call " + call['name'] + "()", color, "Call"])
                    self.metadata[address]["Jump to"] = (fmap, faddress)

            # Implicit function call
            if asmtype == "code":
                branches = (0x10, 0x30, 0x50, 0x70, 0x80, 0x82, 0x90, 0xB0, 0xD0, 0xF0)
                jumps = (0x4C, 0x5C, 0x6C, 0x7C, 0xDC)
                jsr = (0x20, 0x22, 0xfc)
                if code[0] in branches + jumps + jsr:
                    tic = time.time()
                    self.cursor.execute(call_query, (self.smap, self.saddress, map, address))
                    toc = time.time()
                    self.times["call"][address] = toc - tic
                    call = self.cursor.fetchone()
                    if call:
                        line.append([self.spacing, address, "Call " + call['name'] + "()", color, "Call"])
                        self.metadata[address]["Jump to"] = (call['map'], call['begin'])
                if "Jump to" not in self.metadata[address]:
                    if code[0] in branches + (0x4C, 0x5C): # JMP JML
                        jumpaddress = int(self.decoder[code[0]][1](address, m, x, code)[1:], 16)
                        if code[0] != 0x5C: # JML
                            jumpaddress = (address & 0xFF0000) + jumpaddress
                        self.metadata[address]["Jump to"] = (map, jumpaddress)

            # Line comments
            comment = [c for c in comments if c["address"] == address]
            if comment:
                line.append([self.spacing, address, comment[0]['comment'], color, "Comment"])
            elif self.entry_address == address:
                line.append([self.spacing, address, "", color, "Comment"])

            if self.entry_address == address and self.entry_target == "Comment":
                self.entry.delete(0, tkinter.END)
                if comment:
                    self.entry.insert(0, comment[0]['comment'])

            # Data I/O comment
            if asmtype == "code":
                tic = time.time()
                self.cursor.execute(data_query, (self.smap, self.saddress, map, address, function[0]["context"] if function else 0))
                toc = time.time()
                self.times["data"][address] = toc - tic
                for data in self.cursor:
                    line.append([self.spacing, address, "{} - {}".format(self.map_name[data['dmap']], data['comment']), color, "IO"])

            # Decode
            text = "{:{}}".format("Error", self.spacing)
            if asmtype == "code":
                text = "{:06X} {} {}".format(address
                    # mnemonic                  addressing mode
                    , self.decoder[code[0]][0], self.decoder[code[0]][1](address, m, x, code))
                # Alternate mnemonics
                if "BCC" in text and self.items and any(x in self.items[-1][2] for x in ("BEQ", "CMP", "CPX", "CPY")):
                    text = text.replace("BCC", "BLT")
                if "BCS" in text and self.items and any(x in self.items[-1][2] for x in ("BEQ", "CMP", "CPX", "CPY")):
                    text = text.replace("BCS", "BGE")

            elif asmtype == "data":
                if length in (1, 2, 3, 4):
                    length = int(length)
                    ba = code[0:length] + ([0] if length == 3 else [])
                    text = "{:06X} D{} ${num:0{width}X}".format(address
                        , {1:"B",2:"W",3:"L",4:"D"}[length]
                        , num=struct.unpack({1:"<B",2:"<H",3:"<I",4:"<I"}[length]
                            , bytearray(ba))[0]
                        , width=length*2)
                else:
                    # Retrieve bytes
                    tic = time.time()
                    self.cursor.execute(bytes_query, {"smap":self.smap, "saddress":self.saddress, "map":map, "address":address, "length":length - 1})
                    toc = time.time()
                    self.times["big bytes"][address] = toc - tic

                    array = "{:06X} DB ".format(address)
                    data_spacing = len(array)
                    max_len = self.winfo_width() - self.font.measure("$00, ")
                    for data in self.cursor:
                        if self.font.measure(array) > max_len:
                            if "Error" in text:
                                text = array[:-1]
                                max_len -= self.font.measure(' ' * data_spacing)
                            else:
                                line.append([data_spacing, address, array[:-1], color, "Decode"])
                                if (len(line) * self.item_height) > self.winfo_height():
                                    self.cursor.fetchall()
                                    break
                            array = ""
                        array += "${:02X}, ".format(data['byte'])
                    if "Error" in text:
                        text = array[:-2]
                    else:
                        line.append([data_spacing, address, array[:-2], color, "Decode"])
            if len(line):
                line[0][0] = 0
                line[0][2] = "{:{}} {}".format(text, self.spacing - 1, line[0][2])
            else:
                line.append([0, address, text, color, "Decode"])

            self.cache[(map, address)] = (line, self.metadata[address])
            self.items += line

        ptoc = time.time()
        if ptoc - ptic > 0.3:
            print("JDB code page", ptoc - ptic, 1, self.first["address"])
            print("AVG", {k:sum(v.values())/len(v) for k, v in list(self.times.items())})
            print("MAX", {k:max(v.values()) for k, v in list(self.times.items())})
            sums = {k:sum(v.values()) for k, v in list(self.times.items())}
            print("SUM", sums)
            print({k:len(list(v.values())) for k, v in list(self.times.items())})
            if sums.get('function 1',0) >= 0.1:
                pprint.pprint(self.times["function 1"])
            if sums.get('data',0) >= 0.1:
                pprint.pprint(self.times["data"])
            print()

    def setsource(self, smap, saddress):
        if smap != self.smap or saddress != self.saddress:
            self.first_item = 0
            self.smap = smap
            self.saddress = saddress
            self.update_geometry()

    def jump(self, addr):
        if addr == self.first["address"]:
            return

        self.winfo_toplevel().event_generate("<<UpdateJumpList>>")

        first_query = ("SELECT row_num - 1 as item"
                       "  FROM (SELECT ROW_NUMBER() OVER (ORDER BY address) AS row_num, address"
                       "          FROM (SELECT address FROM codemap"
                       "                 WHERE smap = %(smap)s"
                       "                   AND saddress = %(saddress)s"
                       "                 UNION ALL "
                       "                SELECT address FROM comments"
                       "                 WHERE length IS NOT NULL"
                       "                   AND smap = %(smap)s"
                       "                   AND saddress = %(saddress)s"
                       "                 ORDER BY address) AS i) AS o"
                       " WHERE address = %(address)s")
        # TODO maps
        self.cursor.execute(first_query, {"smap":self.smap, "saddress":self.saddress, "address":addr})
        first_item = self.cursor.fetchall()

        if len(first_item):
            self.setfirst(first_item[0]['item'])
        else:
            d = InfoDialog("Info", "Address {:06X} not mapped".format(addr), parent=self.winfo_toplevel())

class ScriptView(CanvasView):
    def __init__(self, parent, cursor=None, **kwargs):
        # TODO will probably need a type table in the future
        source_query = ("SELECT MIN(saddress)"
                       "   FROM bytes"
                       "  WHERE type = 2")
        cursor.execute(source_query)
        self.smap = 1 # probably always 1
        self.saddress = cursor.fetchone()['MIN(saddress)']
        self.dirty = True
        self.buffered = []
        CanvasView.__init__(self, parent, cursor, **kwargs)

    decoder = [
          ("return"         , ())       , ("color crash"      , ())       , ("call event"     , (1, 1))      , ("call event"           , (1, 1))      , ("call event"        , (1, 1))               , ("call PC event"    , (1, 1))               , ("call PC event"     , (1, 1))      , ("call PC event"      , (1, 1)), ("object activation" , ())    , ("object activation", ())                , ("remove object"       , (1,))     , ("script processing", (1,))        , ("script processing", (1,))     , ("npc move props"    , (1,))     , ("npc positioning"      , (1,))     , ("npc facing (up)"      , ())
        , ("jump fwd"       , (1,))     , ("jump back"        , (1,))     , ("if statement"   , (1, 1, 1, 1)), ("if statement"         , (1, 2, 1, 1)), ("if statement"      , (1, 1, 1, 1))         , ("if statement"     , (1, 1, 1, 1))         , ("if statement"      , (1, 1, 1, 1)), ("npc facing (down)"  , ())    , ("check storyline"   , (1, 1)), ("get result"       , (1,))              , ("result"              , (1, 1))   , ("npc facing (left)", ())          , ("get result"       , (2,))     , ("npc facing (right)", ())       , ("npc facing (up)"      , (1,))     , ("npc facing (down)"    , (1,))
        , ("get pc1"        , (1,))     , ("get object coord" , (1, 1, 1)), ("get pc coord"   , (1, 1, 1))   , ("get obj facing"       , (1, 1))      , ("get pc facing"     , (1, 1))               , ("npc facing (left)", (1,))                 , ("npc facing (right)", (1,))        , ("check object status", (1, 1)), ("check battle range", (1, 1)), ("load ascii text"  , (1,))              , ("unknown"             , ())       , ("unknown"          , ())          , ("unknown"          , (1, 1))   , ("check any btn"     , (1,))     , ("color math"           , )         , ("unknown"              , (1, 1))
        , ("check Dash btn" , (1,))     , ("check Confirm btn", (1,))     , ("unknown"        , ())          , ("change palette"       , (1,))        , ("check a btn"       , (1,))                 , ("check b btn"      , (1,))                 , ("check x btn"       , (1,))        , ("check y btn"        , (1,))  , ("check l btn"       , (1,))  , ("check r btn"      , (1,))              , ("alias"               , ())       , ("check Dash btn"   , (1,))        , ("check Confirm btn", (1,))     , ("alias"             , ())       , ("alias"                , ())       , ("check a btn"          , (1,))
        , ("check b btn"    , (1,))     , ("check x btn"      , (1,))     , ("check y btn"    , (1,))        , ("check l btn"          , (1,))        , ("check r btn"       , (1,))                 , ("alias"            , ())                   , ("alias"             , ())          , ("animation limiter"  , (1,))  , ("assignment"        , (3, 1)), ("assignment"       , (3, 1))            , ("assignment"          , (3, 1))   , ("assignment"       , (3, 2))      , ("assignment"       , (3, 1))   , ("assignment"        , (3, 1))   , ("memcpy"               , )         , ("assignment"           , (1, 1))
        , ("assignment"     , (2, 1))   , ("assignment"       , (1, 1))   , ("assignment"     , (1, 1))      , ("assignment"           , (2, 1))      , ("assignment"        , (2, 1))               , ("get storyline ctr", (1,))                 , ("assignment"        , (1, 2))      , ("load crono"         , ())    , ("assignment"        , (1, 2)), ("assignment"       , (1, 2))            , ("assign storyline ctr", (1,))     , ("add"              , (1, 1))      , ("load marle"       , ())       , ("add"               , (1, 1))   , ("add"                  , (1, 1))   , ("subtract"             , (1, 1))
        , ("subtract"       , (2, 1))   , ("subtract"         , (1, 1))   , ("load lucca"     , ())          , ("set bit"              , (1, 1))      , ("reset bit"         , (1, 1))               , ("set bit"          , (1, 1))               , ("reset bit"         , (1, 1))      , ("reset bits"         , (1, 1)), ("load frog"         , ())    , ("set bits"         , (1, 1))            , ("load robo"           , ())       , ("toggle bits"      , (1, 1))      , ("load ayla"        , ())       , ("load magus"        , ())       , ("alias"                , ())       , ("downshift"            , (1, 1))
        , ("alias"          , ())       , ("increment"        , (1,))     , ("increment"      , (1,))        , ("decrement"            , (1,))        , ("alias"             , ())                   , ("set byte"         , (1,))                 , ("set byte"          , (1,))        , ("reset byte"         , (1,))  , ("alias"             , ())    , ("alias"            , ())                , ("npc jump"            , (1, 1, 1)), ("npc jump"         , (1, 1, 1, 1)), ("object drawing"   , (1,))     , ("object drawing"    , (1,))     , ("object drawing"       , ())       , ("random"               , (1,))
        , ("load pc"        , (1,))     , ("load pc"          , (1,))     , ("load npc"       , (1,))        , ("load enemy"           , (1, 1))      , ("npc solid props"   , (1,))                 , ("alias"            , ())                   , ("alias"             , ())          , ("script timing"      , (1,))  , ("memcpy"            , )      , ("set npc speed"    , (1,))              , ("set npc speed"       , (1,))     , ("set object coord" , (1, 1))      , ("set object coord" , (1, 1))   , ("set object coord"  , (2, 2))   , ("sprite priority"      , (1,))     , ("distant object follow", (1,))
        , ("object drawing" , ())       , ("object drawing"   , ())       , ("vector move"    , (1, 1))      , ("alias"                , ())          , ("object follow"     , (1,))                 , ("pc follow"        , (1,))                 , ("move npc"          , (1, 1))      , ("move sprite"        , (1, 1)), ("move to object"    , (1, 1)), ("move to pc"       , (1, 1))            , ("move to coord"       , (1, 1, 1)), ("alias"            , ())          , ("vector move"      , (1, 1))   , ("vector move"       , (1, 1))   , ("vector move to object", (1,))     , ("vector move to pc"    , (1,))
        , ("animated move"  , (1, 1))   , ("animated move"    , (1, 1))   , ("alias"          , ())          , ("alias"                , ())          , ("alias"             , ())                   , ("alias"            , ())                   , ("npc facing"        , (1,))        , ("npc facing"         , (1,))  , ("face object"       , (1,))  , ("face pc"          , (1,))              , ("animation"           , (1,))     , ("animation"        , (1,))        , ("static animation" , (1,))     , ("pause"             , (1,))     , ("reset animation"      , ())       , ("exploration"          , ())
        , ("exploration"    , ())       , ("break"            , ())       , ("end"            , ())          , ("animation"            , ())          , ("animation"         , ())                   , ("move to object"   , (1,))                 , ("move to pc"        , (1,))        , ("loop animation"     , (1, 1)), ("string index"      , (3,))  , ("pause"            , ())                , ("pause"               , ())       , ("personal textbox" , (1,))        , ("pause"            , ())       , ("pause"             , ())       , ("alias"                , ())       , ("alias"                , ())
        , ("dec box auto"   , (1, 1))   , ("textbox top"      , (1,))     , ("textbox bottom" , (1,))        , ("dec box top"          , (1, 1))      , ("dec box bottom"    , (1, 1))               , ("alias"            , ())                   , ("alias"             , ())          , ("add item"           , (1,))  , ("special dialog"    , (1,))  , ("check item"       , (1, 1))            , ("add item"            , (1,))     , ("remove item"      , (1,))        , ("check gold"       , (2, 1))   , ("add gold"          , (2,))     , ("subtract gold"        , (2,))     , ("check recruited pc"   , (1, 1))
        , ("add reserve pc" , (1,))     , ("remove pc"        , (1,))     , ("check active pc", (1, 1))      , ("add active pc"        , (1,))        , ("move pc to reserve", (1,))                 , ("equip item"       , (1, 1))               , ("remove active pc"  , (1,))        , ("get item amount"    , (1, 1)), ("battle"            , (2,))  , ("move party"       , (1, 1, 1, 1, 1, 1)), ("party follow"        , ())       , ("alias"            , ())          , ("change location"  , (2, 1, 1)), ("change location"   , (2, 1, 1)), ("change location"      , (2, 1, 1)), ("change location"      , (2, 1, 1))
        , ("change location", (2, 1, 1)), ("change location"  , (2, 1, 1)), ("change location", (1, 1, 1, 1)), ("explore mode"         , (1,))        , ("copy tiles"        , (1, 1, 1, 1, 1, 1, 1)), ("copy tiles"       , (1, 1, 1, 1, 1, 1, 1)), ("scroll layers"     , (2, 1, 1))   , ("scroll screen"      , (1, 1)), ("play sound"        , (1,))  , ("alias"            , ())                , ("play song"           , (1,))     , ("music volume"     , (1, 1))      , ("all purpose sound", (1, 1, 1)), ("wait for silence"  , ())       , ("wait for song end"    , ())       , ("alias"                , ())
        , ("darken"         , (1,))     , ("color addition"   , )         , ("fade out screen", ())          , ("wait for brighten end", ())          , ("shake"             , (1,))                 , ("alias"            , ())                   , ("alias"             , ())          , ("alias"              , ())    , ("restore hp / mp"   , ())    , ("restore hp"       , ())                , ("restore mp"          , ())       , ("alias"            , ())          , ("alias"            , ())       , ("alias"             , ())       , ("gfx (17 args)"        , )         , ("mode 7 scene"         , )
        ]

    def item_generate(self):
        if not self.page_size:
            return

        if not self.dirty:
            self.items = self.buffered[self.first_item:self.first_item+self.page_size]
        else:
            self.times.clear()
            del self.buffered[:]
            self.items_len = 0

            ptic = time.time()
            script_query = ("SELECT byte"
                            "  FROM bytes"
                            " WHERE smap = ?"
                            "   AND saddress = ?"
                            " ORDER BY address")
            tic = time.time()
            self.cursor.execute(script_query, (self.smap, self.saddress))
            code = [row['byte'] for row in self.cursor]
            toc = time.time()
            self.times["script"][-1] = toc - tic

            # For each address...
            code_iter = iter(enumerate(code))
            event_count = 0
            for address, byte in code_iter:
                # Decode
                if address == 0x00:
                    event_count = struct.unpack("<B", bytearray(code[address:address+1]))[0]
                    text = "{:06X} ${:02X}".format(address, event_count)
                    self.buffered.append([0, address, text, -1.0, "Script"])
                elif address < (event_count * 16 * 2):
                    text = "{:06X} ${:04X}".format(address, struct.unpack("<H", bytearray(code[address:address+2]))[0])
                    next(code_iter)
                    self.buffered.append([0, address, text, -1.0, "Script"])
                else:
                    text = "{:06X} {:02X} {}".format(address, byte, self.decoder[byte][0])
                    def unpack_bytes(address, length):
                        ba = code[address:address+length] + ([0] if length == 3 else [])
                        next(itertools.islice(code_iter,length,length), None)
                        return struct.unpack({1:"<B",2:"<H",3:"<I",4:"<I"}[length]
                            , bytearray(ba))[0]
                    if len(self.decoder[byte]) == 1:
                        if byte == 0x4E:
                            text += " ${:04X}".format(unpack_bytes(address+1,2))
                            text += " ${:02X}".format(unpack_bytes(address+3,1))
                            count = unpack_bytes(address+4,2)
                            text += " ${:04X}".format(count)
                            count = count + 2
                            for i in range(count):
                                text += " ${:02X}".format(unpack_bytes(address+6+i,1))
                        else:
                            text += " fixme"
                    else:
                        sum = 1
                        for length in self.decoder[byte][1]:
                            text += " ${num:0{width}X}".format(num=unpack_bytes(address+sum, length), width=length*2)
                            sum = sum + length
                    self.buffered.append([0, address, text, -1.0, "Script"])

            self.items_len = len(self.buffered)
            self.items = self.buffered[:self.page_size]
            self.dirty = False

            ptoc = time.time()
            if ptoc - ptic > 0.3:
                print("JDB script page", ptoc - ptic)
                print({k:sum(v.values())/len(v) for k, v in list(self.times.items())})
                print({k:max(v.values()) for k, v in list(self.times.items())})
                print({k:sum(v.values()) for k, v in list(self.times.items())})
                print({k:len(list(v.values())) for k, v in list(self.times.items())})
                print(sum({k:sum(v.values()) for k, v in list(self.times.items())}.values()))
                print()

    def setsource(self, smap, saddress):
        if smap != self.smap or saddress != self.saddress:
            self.first_item = 0
            self.smap = smap
            self.saddress = saddress
            self.dirty = True
            self.update_geometry()

    def jump(self, addr):
        self.winfo_toplevel().event_generate("<<UpdateJumpList>>")
        self.setfirst(addr)

class WRAMView(CanvasView):
    def __init__(self, parent, cursor=None, **kwargs):
        self.map_name = {1:"ROM", 2:"WRAM", 3:"SRAM", 4:"VRAM", 5:"REG"
                        ,"ROM":1, "WRAM":2, "SRAM":3, "VRAM":4, "REG":5}

        CanvasView.__init__(self, parent, cursor, **kwargs)

        self.items_len = 0x01ffff
        self.max_address = 0x01ffff

    def item_generate(self):
        self.times.clear()
        del self.items[:]
        self.metadata.clear()

        ptic = time.time()
        page_query = ("SELECT dm.dmap, dm.daddress, dm.cmap, dm.caddress, f.name, c.comment"
                      "  FROM datamap dm"
                      "  LEFT JOIN functions f ON (dm.csmap = f.smap AND dm.csaddress = f.saddress"
                      "                        AND dm.cmap = f.map AND dm.caddress >= f.begin AND dm.caddress <= f.end)"
                      "  LEFT JOIN comments c ON (0 = c.smap AND 0 = c.saddress"
                      "                       AND dm.dmap = c.map AND dm.daddress = c.address)"
                      " WHERE dm.dmap = ?"
                      "   AND dm.daddress >= ?"
                      " ORDER BY                         dm.dmap, dm.daddress"
                      "        , dm.csmap, dm.csaddress, dm.cmap, dm.caddress"
                      " LIMIT ?")
        tic = time.time()
        self.cursor.execute(page_query, (2, self.first_item, self.page_size))
        rows = self.cursor.fetchall()
        toc = time.time()
        self.times["query"][-1] = toc - tic

        # For each address...
        i = 0
        for daddress in range(self.first_item, min(self.first_item + self.page_size, 0x20000)):
            if len(self.items) > self.page_size:
                break

            if (2, daddress) in self.cache:
                line, meta = self.cache[(dmap, daddress)]
                self.items += line
                self.metadata[daddress] = meta
                continue

            line = []

            # Decode
            comment = ""
            while i < len(rows) and rows[i]['daddress'] == daddress:
                comment = rows[i]['comment'] if rows[i]['comment'] else ""
                name = rows[i]['name'] + "()" if rows[i]['name'] else ""
                line.append([self.spacing, daddress, "{}:{:06X} - {}".format(self.map_name[rows[i]['cmap']], rows[i]['caddress'], name), daddress / 16.0, "WRAM"])
                i = i + 1

            text = "{:06X}".format(daddress)
            if len(line):
                line[0][0] = 0
                line[0][2] = "{:{}} {} {}".format(text, self.spacing - 1, line[0][2], comment)
            else:
                line.append([0, daddress, "{:{}} {}".format(text, self.spacing - 1, comment), daddress / 16.0, "WRAM"])

            self.cache[(map, daddress)] = (line, self.metadata[daddress])
            self.items += line

        ptoc = time.time()
        if ptoc - ptic > 0.3:
            print("JDB WRAM page", ptoc - ptic)
            print({k:sum(v.values())/len(v) for k, v in list(self.times.items())})
            print({k:max(v.values()) for k, v in list(self.times.items())})
            print({k:sum(v.values()) for k, v in list(self.times.items())})
            print({k:len(list(v.values())) for k, v in list(self.times.items())})
            print(sum({k:sum(v.values()) for k, v in list(self.times.items())}.values()))
            print()

    def jump(self, addr):
        self.winfo_toplevel().event_generate("<<UpdateJumpList>>")
        self.setfirst(addr)

class DB(object):
    def __init__(self):
        self.database = None
        self.cursor = None
        self.reconnect()

    def __iter__(self):
        return self.cursor.__iter__()

    def __next__(self):
        return self.cursor__next__()

    def reconnect(self):
        if self.cursor:
            self.cursor.close()

        if self.database:
            self.database.close()

        self.database = mariadb.connect(host="localhost", database="ct", user="root", password="1234")
        self.cursor = self.database.cursor(dictionary=True)

    def execute(self, sql, params=None):
        try:
            self.cursor.execute(sql, params)
        except mariadb.OperationalError as e:
            if e[0] == 2055:
                self.reconnect()
                self.cursor.execute(sql, params)
            else:
                raise

    def fetchall(self, *args, **kwargs):
        return self.cursor.fetchall(*args, **kwargs)

    def fetchone(self, *args, **kwargs):
        return self.cursor.fetchone(*args, **kwargs)

    def commit(self):
        self.database.commit()

class Annotate(tkinter.Tk):
    def publish(self, event):
        for widget in self.subscriptions[event]:
            widget.event_generate("<<Publish"+event[2:])

    def subscribe(self, widget, event, callback):
        self.bind(event, lambda e: self.publish(event))
        self.subscriptions[event].add(widget)
        widget.bind("<<Publish"+event[2:], callback)

    def __init__(self, **kwargs):
        tkinter.Tk.__init__(self, **kwargs)
        self.geometry("{}x{}+0+40".format(self.winfo_screenwidth()-15, self.winfo_screenheight()//2-40-15))
        #self.font = tkinter.font.Font(family="Consolas", size="14")
        self.font = tkinter.font.Font(family="Inconsolata", size="20")
        self.cursor = DB()

        self.subscriptions = collections.defaultdict(set) # event:{widgets}

        # Paned Window
        panedwindow = tkinter.PanedWindow(self)
        panedwindow.pack(fill=tkinter.BOTH, expand=True)

        # Code Notebook
        codenotebook = tkinter.ttk.Notebook(panedwindow)
        codenotebook.bind("<<NotebookTabChanged>>", lambda e: self.event_generate("<<CodeNotebookTabChanged>>"))
        def updatecodenotebook(event):
            data = sourcelistbox.get(tkinter.ANCHOR).split(":", 1)
            if data[0]:
                self.canvas.setsource(data[0], int(data[1], 16))
        self.subscribe(codenotebook, "<<SourceChanged>>", updatecodenotebook)

        codenotebook.pack(side=tkinter.LEFT, fill=tkinter.BOTH, expand=True)

        # ASM Frame
        asmframe = tkinter.Frame(codenotebook, borderwidth=2, relief=tkinter.SUNKEN)
        asmyscroll = tkinter.Scrollbar(asmframe)
        asmxscroll = tkinter.Scrollbar(asmframe, orient=tkinter.HORIZONTAL)
        asmxscroll.set(0.0, 1.0)

        asmcanvas = ASMView(asmframe, self.cursor, borderwidth=0, yscroll=asmyscroll
            , xscroll = asmxscroll, highlightthickness=False, font=self.font)

        self.canvas = asmcanvas

        asmxscroll.config(command=asmcanvas.xview)
        asmxscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

        asmyscroll.config(command=asmcanvas.yview)
        asmyscroll.grid(row=0, column=1, sticky=tkinter.N+tkinter.S)

        asmcanvas.grid(row=0, column=0, sticky=tkinter.N+tkinter.S+tkinter.E+tkinter.W)

        asmframe.rowconfigure(0, weight=1)
        asmframe.columnconfigure(0, weight=1)

        codenotebook.add(asmframe, text="ASM")

        # Script Frame
        scriptframe = tkinter.Frame(codenotebook, borderwidth=2, relief=tkinter.SUNKEN)
        scriptyscroll = tkinter.Scrollbar(scriptframe)
        scriptxscroll = tkinter.Scrollbar(scriptframe, orient=tkinter.HORIZONTAL)
        scriptxscroll.set(0.0, 1.0)

        scriptcanvas = ScriptView(scriptframe, self.cursor, borderwidth=0, yscroll=scriptyscroll
            , xscroll=scriptxscroll, highlightthickness=False, font=self.font)

        scriptxscroll.config(command=scriptcanvas.xview)
        scriptxscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

        scriptyscroll.config(command=scriptcanvas.yview)
        scriptyscroll.grid(row=0, column=1, sticky=tkinter.N+tkinter.S)

        scriptcanvas.grid(row=0, column=0, sticky=tkinter.N+tkinter.S+tkinter.E+tkinter.W)

        scriptframe.rowconfigure(0, weight=1)
        scriptframe.columnconfigure(0, weight=1)

        codenotebook.add(scriptframe, text="Script")

        # WRAM Frame
        wramframe = tkinter.Frame(codenotebook, borderwidth=2, relief=tkinter.SUNKEN)
        wramyscroll = tkinter.Scrollbar(wramframe)
        wramxscroll = tkinter.Scrollbar(wramframe, orient=tkinter.HORIZONTAL)
        wramxscroll.set(0.0, 1.0)

        wramcanvas = WRAMView(wramframe, self.cursor, borderwidth=0, yscroll=wramyscroll
            , xscroll=wramxscroll, highlightthickness=False, font=self.font)

        wramxscroll.config(command=wramcanvas.xview)
        wramxscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

        wramyscroll.config(command=wramcanvas.yview)
        wramyscroll.grid(row=0, column=1, sticky=tkinter.N+tkinter.S)

        wramcanvas.grid(row=0, column=0, sticky=tkinter.N+tkinter.S+tkinter.E+tkinter.W)

        wramframe.rowconfigure(0, weight=1)
        wramframe.columnconfigure(0, weight=1)

        codenotebook.add(wramframe, text="WRAM")

        panedwindow.add(codenotebook)

        # Data Notebook
        datanotebook = tkinter.ttk.Notebook(panedwindow)
        datanotebook.pack(side=tkinter.LEFT, fill=tkinter.BOTH, expand=True)

        # Data Frame
        ioframe = tkinter.Frame(datanotebook, borderwidth=2, relief=tkinter.SUNKEN)
        ioentry = tkinter.Entry(ioframe, font=self.font)
        self.subscribe(ioentry, "<<AddressChanged>>", lambda e: ioentry.delete(0, tkinter.END))
        def updateioentry(event):
            data = iolistbox.get(tkinter.ANCHOR).split(" - ", 1)
            if data[0]:
                ioentry.delete(0, tkinter.END)
                ioentry.insert(0, data[1])
                state = "readonly" if data[0].startswith("REG") else tkinter.NORMAL
                ioentry.configure(state=state)
        self.subscribe(ioentry, "<<UpdateIOEntry>>", updateioentry)
        ioentry.bind("<Return>", lambda e: self.event_generate("<<CommitIOEntry>>"))
        ioentry.pack(side=tkinter.TOP, fill=tkinter.X, expand=False)

        iolistboxframe = tkinter.Frame(ioframe)
        iolistboxyscroll = tkinter.Scrollbar(iolistboxframe)
        iolistboxxscroll = tkinter.Scrollbar(iolistboxframe, orient=tkinter.HORIZONTAL)
        iolistboxxscroll.set(0.0, 1.0)

        iolistbox = DataView(iolistboxframe
            , self.cursor, borderwidth=0, yscroll=iolistboxyscroll, xscroll=iolistboxxscroll
            , font=self.font, exportselection=False)
        self.subscribe(iolistbox, "<<AddressChanged>>", lambda e: iolistbox.setaddress(1, int(self.canvas.io_address)))
        iolistbox.bind("<ButtonRelease-1>", lambda e: self.event_generate("<<UpdateIOEntry>>"))
        def updateiolist(event):
            if iolistbox.size() != iolistbox.index(tkinter.ANCHOR) and ioentry.cget("state") != "readonly":
                data = iolistbox.get(tkinter.ANCHOR).split(" - ", 1)
                if data[1] != ioentry.get():
                    iolistbox.delete(tkinter.ANCHOR)
                    iolistbox.insert(tkinter.ANCHOR, "{} - {}".format(data[0], ioentry.get()))
                    iolistbox.selection_set(iolistbox.index(tkinter.ANCHOR) - 1)
                    iolistbox.selection_anchor(iolistbox.index(tkinter.ANCHOR) - 1)
        self.subscribe(iolistbox, "<<CommitIOEntry>>", updateiolist)

        iolistboxxscroll.config(command=iolistbox.xview)
        iolistboxxscroll.grid(row=1, column=0, sticky=tkinter.E+tkinter.W)

        iolistboxyscroll.config(command=iolistbox.yview)
        iolistboxyscroll.grid(row=0, column=1, sticky=tkinter.N+tkinter.S)

        iolistbox.grid(row=0, column=0, sticky=tkinter.N+tkinter.S+tkinter.E+tkinter.W)

        iolistboxframe.rowconfigure(0, weight=1)
        iolistboxframe.columnconfigure(0, weight=1)

        iolistboxframe.pack(side=tkinter.TOP, fill=tkinter.BOTH, expand=True)

        datanotebook.add(ioframe, text="I/O")

        # Jump Frame
        jumpframe = tkinter.Frame(datanotebook, borderwidth=2, relief=tkinter.SUNKEN)
        jumpscroll = tkinter.Scrollbar(jumpframe)
        jumplistbox = tkinter.Listbox(jumpframe
            , borderwidth=0, yscrollcommand=jumpscroll.set, font=self.font, exportselection=False)
        jumplistbox.bind("<Double-1>", lambda e: self.event_generate("<<Jump>>"))

        def jumplistbox_insert(event):
            jumplistbox.insert(tkinter.END
                , "{:06X} - {}".format(self.canvas.first["address"]
                    , self.canvas.metadata[self.canvas.first["address"]].get("Function","None")))
            jumplistbox.see(tkinter.END)
        self.subscribe(jumplistbox, "<<UpdateJumpList>>", jumplistbox_insert)

        jumplistbox.pack(side=tkinter.LEFT, fill=tkinter.BOTH, expand=True)

        jumpscroll.config(command=jumplistbox.yview)
        jumpscroll.pack(side=tkinter.LEFT, fill=tkinter.Y)

        datanotebook.add(jumpframe, text="Jump")

        # Source Frame
        sourceframe = tkinter.Frame(datanotebook, borderwidth=2, relief=tkinter.SUNKEN)
        sourcescroll = tkinter.Scrollbar(sourceframe)
        sourcelistbox = tkinter.Listbox(sourceframe
            , borderwidth=0, yscrollcommand=sourcescroll.set, font=self.font, exportselection=False)

        sourcelistbox.bind("<ButtonRelease-1>", lambda e: self.event_generate("<<SourceChanged>>"))
        def updatesourcelistbox(event):
            try:
                lookup = {"ASM":1, "Script":2, "WRAM":None} 
                type = lookup[codenotebook.tab("current", "text")]
                bytes_query = ("SELECT DISTINCT smap, saddress"
                               "  FROM bytes"
                               " WHERE type = ?"
                               " ORDER BY saddress")
                self.cursor.execute(bytes_query, (type,))
                sourcelistbox.delete(0, tkinter.END)
                for row in self.cursor:
                    sourcelistbox.insert(tkinter.ANCHOR, "{}:{:06X}".format(*list(row.values())))
            except KeyError:
                sourcelistbox.delete(0, tkinter.END)
        self.subscribe(sourcelistbox, "<<CodeNotebookTabChanged>>", updatesourcelistbox)
        updatesourcelistbox(None)

        sourcelistbox.pack(side=tkinter.LEFT, fill=tkinter.BOTH, expand=True)

        sourcescroll.config(command=sourcelistbox.yview)
        sourcescroll.pack(side=tkinter.LEFT, fill=tkinter.Y)

        datanotebook.add(sourceframe, text="Source")

        def tabpreload(event):
            # callbacks won't fire until tab is loaded
            for tab_id in range(1, datanotebook.index("end")):
                datanotebook.after(50*tab_id, datanotebook.select, tab_id)
            datanotebook.after(50*datanotebook.index("end"), datanotebook.select, 0)
            datanotebook.unbind('<Map>')
        datanotebook.bind("<Map>", tabpreload)

        panedwindow.add(datanotebook)

        def sashit(event):
            panedwindow.sash_place(0, int(panedwindow.winfo_width()*0.66), 0)
            panedwindow.unbind('<Map>')
        panedwindow.bind('<Map>', lambda e: panedwindow.after(50, sashit, e))

        # root

        def jump_to_entry(event):
            addr = int(jumplistbox.get(tkinter.ANCHOR).split(" - ", 1)[0], 16)
            if addr is not None:
                self.canvas.jump(addr)
        self.bind("<<Jump>>", jump_to_entry)

        def jump_to_dialog(event):
            addr = askintegerliteral("Input", "Jump to addr:"
                , parent=self, minvalue=0, maxvalue=self.canvas.max_address)
            if addr is not None:
                self.canvas.jump(addr)
        self.bind("<Control-g>", jump_to_dialog)

        def mouse_wheel(event):
            if not isinstance(event.widget, TkinterView):
                return
            if event.num == 5:
                event.widget.yview("scroll", 1, "wheel")
            elif event.num == 4:
                event.widget.yview("scroll", -1, "wheel")
            else:
                event.widget.yview("scroll", -1*round(event.delta/120), "wheel")
        self.bind_all("<MouseWheel>", mouse_wheel)
        self.bind_all("<Button-4>", mouse_wheel)
        self.bind_all("<Button-5>", mouse_wheel)

        def keyboard_scroll(event):
            if not isinstance(event.widget, TkinterView):
                return
            if event.char == 'j' or event.keysym == "Down":
                event.widget.yview("scroll", 1, "wheel")
            elif event.char == 'k' or event.keysym == "Up":
                event.widget.yview("scroll", -1, "wheel")
        self.bind_all("<Down>", keyboard_scroll)
        self.bind_all("<Up>", keyboard_scroll)
        self.bind_all("j", keyboard_scroll)
        self.bind_all("k", keyboard_scroll)

        def refresh(event):
            self.canvas.cache.clear()
            self.cursor.commit()
            self.canvas.update_geometry()
            self.iolistbox.update_geometry()
        self.bind("<F5>", refresh)

        def insertmode(event):
            self.unbind_all("j")
            self.unbind_all("k")
        self.bind("<<EntryActive>>", insertmode)

        def commandmode(event):
            self.bind_all("j", keyboard_scroll)
            self.bind_all("k", keyboard_scroll)

        def commit_comment(smap, saddress, map, address, context, comment):
            comment_insert = ("INSERT INTO comments"
                              "       (smap, saddress, map, address, context, comment, length)"
                              "VALUES (%(smap)s, %(saddress)s, %(map)s, %(address)s, %(context)s, %(comment)s, NULL)"
                              "    ON DUPLICATE KEY UPDATE"
                              "       comment = %(comment)s")
            comment_delete = ("DELETE FROM comments"
                              " WHERE smap = ?"
                              "   AND saddress = ?"
                              "   AND map = ?"
                              "   AND address = ?"
                              "   AND context = ?")
            if comment:
                self.cursor.execute(comment_insert, {"smap":smap, "saddress":saddress, "map":map, "address":address, "context":context, "comment":comment})
            else:
                self.cursor.execute(comment_delete, (smap, saddress, map, address, context))
            self.cursor.commit()
            
        def commit_function(map, address, comment):
            function_update = ("UPDATE functions"
                               "   SET name = ?"
                               " WHERE map = ?"
                               "   AND begin = ?")
            self.cursor.execute(function_update, (comment, map, address))
            self.cursor.commit()

        def commit_entry(e):
            # TODO map = current view
            if self.canvas.entry_target == "Comment":
                commit_comment(self.canvas.smap, self.canvas.saddress, 1, self.canvas.entry_address, 0, self.canvas.entry.get())
            else:
                commit_function(1, self.canvas.entry_address, self.canvas.entry.get())
            self.commandmode(e)
            self.canvas.event_generate("<<RemoveEntry>>")
        self.bind("<<CommitEntry>>", commit_entry)

        def commit_ioentry(e):
            data = iolistbox.get(tkinter.ANCHOR).split(" - ", 1)
            map = self.canvas.map_name[data[0].split()[0]]
            address = int(data[0].split()[2], 16)
            commit_comment(0, 0, map, address, self.canvas.metadata[iolistbox.caddress]["Context"], ioentry.get())
            self.canvas.cache.clear()
            self.canvas.update_geometry()
        self.bind("<<CommitIOEntry>>", commit_ioentry, add='+')

        def setactivecanvas(event):
            lookup = {"ASM":asmcanvas, "Script":scriptcanvas, "WRAM":wramcanvas}
            self.canvas = lookup[codenotebook.tab("current", "text")]
        self.bind("<<CodeNotebookTabChanged>>", setactivecanvas, add='+')

if __name__ == "__main__":
    window = Annotate()
    window.mainloop()

