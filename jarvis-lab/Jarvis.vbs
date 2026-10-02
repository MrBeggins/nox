' Тихий запуск Джарвиса без единого консольного окна.
' Запускает start-jarvis.ps1 скрыто (0 = hidden window).
CreateObject("WScript.Shell").Run "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""C:\jarvis-lab\start-jarvis.ps1""", 0, False
