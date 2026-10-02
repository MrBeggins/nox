# -*- coding: utf-8 -*-
"""
Показывает на экране КРАСНЫЕ «плюсы» (crosshair) в заданных точках клика — чтобы пользователь
видел, куда поставлены координаты стаканов. Прозрачное, поверх всего, КЛИКО-ПРОЗРАЧНОЕ окно на
весь виртуальный рабочий стол (все мониторы, в т.ч. отрицательные координаты). Само гаснет ~3.5с.
Запуск: python nox_marker.py x1 y1 [x2 y2 ...]
"""
import sys, ctypes

# DPI-осознанность (координаты в физических пикселях на всех мониторах)
try:
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import tkinter as tk

def main():
    nums = [int(float(a)) for a in sys.argv[1:]]
    pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    if not pts:
        return
    GSM = ctypes.windll.user32.GetSystemMetrics
    vx, vy = GSM(76), GSM(77)          # SM_XVIRTUALSCREEN / SM_YVIRTUALSCREEN
    vcx, vcy = GSM(78), GSM(79)        # ширина/высота всего виртуального стола

    KEY = "#010203"                    # цвет-ключ прозрачности
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.config(bg=KEY)
    try:
        root.attributes("-transparentcolor", KEY)
    except Exception:
        root.attributes("-alpha", 0.55)
    cv = tk.Canvas(root, width=vcx, height=vcy, bg=KEY, highlightthickness=0)
    cv.pack()

    R = 16   # половина «плюса» (~32px, размер курсора)
    for (px, py) in pts:
        x, y = px - vx, py - vy
        cv.create_line(x - R, y, x + R, y, fill="#ff3b3b", width=4, capstyle="round")
        cv.create_line(x, y - R, x, y + R, fill="#ff3b3b", width=4, capstyle="round")
        cv.create_oval(x - R, y - R, x + R, y + R, outline="#ff3b3b", width=2)

    root.update_idletasks(); root.update()

    # разместить окно по абсолютным координатам всего виртуального стола (tk geometry ломается на минусах)
    hwnd = root.winfo_id()
    try:
        root.after(0, lambda: None)
        ga_root = ctypes.windll.user32.GetAncestor(hwnd, 2)  # GA_ROOT
        if ga_root:
            hwnd = ga_root
    except Exception:
        pass
    HWND_TOPMOST = -1
    SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x10, 0x40
    ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, vx, vy, vcx, vcy,
                                      SWP_NOACTIVATE | SWP_SHOWWINDOW)
    # клико-прозрачность (клики проходят сквозь маркер)
    GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
    try:
        ex = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED | WS_EX_TRANSPARENT)
    except Exception:
        pass

    root.after(3500, root.destroy)
    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
