# -*- coding: utf-8 -*-
"""
Показывает на экране КРАСНЫЕ «плюсы» (crosshair) в заданных точках клика — видно, куда поставлены
координаты стаканов. Прозрачное (colorkey по чёрному), поверх всего, КЛИКО-ПРОЗРАЧНОЕ окно на весь
виртуальный рабочий стол (все мониторы, в т.ч. отрицательные координаты). Гаснет ~4с.
Запуск: python nox_marker.py x1 y1 [x2 y2 ...]
"""
import sys, ctypes

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
U = ctypes.windll.user32

def main():
    nums = [int(float(a)) for a in sys.argv[1:]]
    pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    if not pts:
        return
    GSM = U.GetSystemMetrics
    vx, vy, vcx, vcy = GSM(76), GSM(77), GSM(78), GSM(79)

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.config(bg="black")
    cv = tk.Canvas(root, width=vcx, height=vcy, bg="black", highlightthickness=0, bd=0)
    cv.pack()
    R, W = 18, 6
    for (px, py) in pts:
        x, y = px - vx, py - vy
        cv.create_line(x - R, y, x + R, y, fill="#ff2d2d", width=W, capstyle="round")
        cv.create_line(x, y - R, x, y + R, fill="#ff2d2d", width=W, capstyle="round")
        cv.create_oval(x - R - 4, y - R - 4, x + R + 4, y + R + 4, outline="#ff2d2d", width=2)
    root.update_idletasks(); root.update()

    # HWND верхнего окна (для overrideredirect — само окно root)
    hwnd = U.GetAncestor(cv.winfo_id(), 2) or root.winfo_id()   # GA_ROOT

    GWL_EXSTYLE = -20
    WS_EX_LAYERED, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW, WS_EX_TOPMOST = 0x80000, 0x20, 0x80, 0x8
    ex = U.GetWindowLongW(hwnd, GWL_EXSTYLE)
    U.SetWindowLongW(hwnd, GWL_EXSTYLE,
                     ex | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)

    HWND_TOPMOST = -1
    SWP_NOACTIVATE, SWP_SHOWWINDOW, SWP_FRAMECHANGED = 0x10, 0x40, 0x20
    U.SetWindowPos(hwnd, HWND_TOPMOST, vx, vy, vcx, vcy,
                   SWP_NOACTIVATE | SWP_SHOWWINDOW | SWP_FRAMECHANGED)

    # ВАЖНО: colorkey выставляем ПОСЛЕ смены ex-стиля (иначе слоевые атрибуты сбрасываются -> окно пропадает).
    LWA_COLORKEY = 0x1
    U.SetLayeredWindowAttributes(hwnd, 0x00000000, 0, LWA_COLORKEY)  # чёрный -> прозрачный

    root.after(4000, root.destroy)
    root.mainloop()

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
