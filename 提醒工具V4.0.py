"""
到期提醒工具 v3.1
优化版本 - 修复了多线程、数据持久化、日志等核心问题
"""

import json
import os
import time
import threading
import logging
import shutil
import sys
from datetime import datetime, timedelta
from tkinter import *
from tkinter import ttk, messagebox, filedialog
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, asdict, field
import getpass
import subprocess

# 第三方库导入
from plyer import notification
import pystray
from PIL import Image, ImageDraw

# 尝试导入日历控件
try:
    from tkcalendar import DateEntry

    HAS_CALENDAR = True
except ImportError:
    HAS_CALENDAR = False
    print("提示：安装 tkcalendar 可以获得更好的日期选择体验")
    print("运行：pip install tkcalendar")


# ==================== 常量定义 ====================
class Constants:
    APP_NAME = "ExpiryReminder"
    APP_VERSION = "3.1"
    DATA_FILE = "reminders.json"
    DATA_BACKUP_FILE = "reminders_backup.json"
    CONFIG_FILE = "config.json"
    LOG_FILE = "expiry_reminder.log"

    # 默认配置
    DEFAULT_CONFIG = {
        "default_advance_days": 1,
        "enable_notification": True,
        "check_interval": 3600,
        "close_action": "ask",
        "auto_start": False,
        "notify_frequency": "hourly",
        "backup_enabled": True,
        "max_backups": 5
    }

    # 颜色标记
    COLOR_EXPIRED = "red"
    COLOR_WARNING = "orange"
    COLOR_TODAY = "red"


# ==================== 日志配置 ====================
def setup_logging():
    """配置日志系统"""
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.FileHandler(Constants.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler()
        ]
    )
    return logging.getLogger(__name__)


logger = setup_logging()


# ==================== 数据模型 ====================
@dataclass
class Reminder:
    """提醒数据模型"""
    name: str
    expiry_date: str
    platform: str = ""
    advance_days: Optional[int] = None
    notified_days: List[int] = field(default_factory=list)
    last_notify_time: Optional[float] = None
    last_notify_date: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'Reminder':
        # 兼容旧版本数据
        if 'notified' in data:
            # 迁移旧数据
            notified_days = []
            if data.get('notified', False):
                notified_days = [-1]  # 旧逻辑标记为已通知
        else:
            notified_days = data.get('notified_days', [])

        return cls(
            name=data.get('name', ''),
            expiry_date=data.get('expiry_date', ''),
            platform=data.get('platform', ''),
            advance_days=data.get('advance_days'),
            notified_days=notified_days,
            last_notify_time=data.get('last_notify_time'),
            last_notify_date=data.get('last_notify_date')
        )


# ==================== 数据管理器 ====================
class DataManager:
    """数据管理器 - 负责数据的读写、备份和迁移"""

    def __init__(self):
        self.lock = threading.RLock()
        self.reminders: List[Reminder] = []
        self.config: Dict[str, Any] = {}
        self.last_refresh_date = datetime.now().date()
        self._load_all()

    def _load_all(self):
        """加载所有数据"""
        self.config = self._load_config()
        self.reminders = self._load_reminders()
        logger.info(f"加载了 {len(self.reminders)} 个提醒，配置: {self.config}")

    def _load_config(self) -> Dict[str, Any]:
        """加载配置文件"""
        config = Constants.DEFAULT_CONFIG.copy()
        if os.path.exists(Constants.CONFIG_FILE):
            try:
                with open(Constants.CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    config.update(loaded)
            except Exception as e:
                logger.error(f"加载配置文件失败: {e}")
        return config

    def save_config(self):
        """保存配置"""
        with self.lock:
            try:
                with open(Constants.CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(self.config, f, ensure_ascii=False, indent=2)
                logger.info("配置已保存")
            except Exception as e:
                logger.error(f"保存配置失败: {e}")

    def _load_reminders(self) -> List[Reminder]:
        """加载提醒数据"""
        if not os.path.exists(Constants.DATA_FILE):
            return []

        try:
            with open(Constants.DATA_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [Reminder.from_dict(item) for item in data]
        except json.JSONDecodeError as e:
            logger.error(f"数据文件损坏: {e}")
            # 尝试从备份恢复
            if os.path.exists(Constants.DATA_BACKUP_FILE):
                logger.info("尝试从备份恢复...")
                try:
                    with open(Constants.DATA_BACKUP_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        return [Reminder.from_dict(item) for item in data]
                except Exception as backup_e:
                    logger.error(f"从备份恢复失败: {backup_e}")
        except Exception as e:
            logger.error(f"加载数据失败: {e}")

        return []

    def save_reminders(self):
        """保存提醒数据（带备份）"""
        with self.lock:
            try:
                # 备份现有文件
                if os.path.exists(Constants.DATA_FILE) and self.config.get('backup_enabled', True):
                    shutil.copy2(Constants.DATA_FILE, Constants.DATA_BACKUP_FILE)
                    logger.info("数据已备份")

                # 原子写入（先写临时文件）
                temp_file = Constants.DATA_FILE + ".tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in self.reminders], f, ensure_ascii=False, indent=2)

                # 原子替换
                if os.path.exists(temp_file):
                    if os.path.exists(Constants.DATA_FILE):
                        os.remove(Constants.DATA_FILE)
                    os.rename(temp_file, Constants.DATA_FILE)

                logger.info(f"保存了 {len(self.reminders)} 个提醒")
            except Exception as e:
                logger.error(f"保存数据失败: {e}")
                # 尝试从备份恢复
                if os.path.exists(Constants.DATA_BACKUP_FILE):
                    shutil.copy2(Constants.DATA_BACKUP_FILE, Constants.DATA_FILE)
                    logger.info("已从备份恢复数据")

    def add_reminder(self, reminder: Reminder):
        """添加提醒"""
        with self.lock:
            self.reminders.append(reminder)
            self.save_reminders()
            logger.info(f"添加提醒: {reminder.name}")

    def update_reminder(self, index: int, reminder: Reminder) -> bool:
        """更新提醒"""
        with self.lock:
            if 0 <= index < len(self.reminders):
                self.reminders[index] = reminder
                self.save_reminders()
                logger.info(f"更新提醒: {reminder.name}")
                return True
            return False

    def delete_reminder(self, index: int):
        """删除提醒"""
        with self.lock:
            if 0 <= index < len(self.reminders):
                name = self.reminders[index].name
                del self.reminders[index]
                self.save_reminders()
                logger.info(f"删除提醒: {name}")

    def get_reminder(self, index: int) -> Optional[Reminder]:
        """获取提醒"""
        with self.lock:
            if 0 <= index < len(self.reminders):
                return self.reminders[index]
            return None

    def get_all_reminders(self) -> List[Reminder]:
        """获取所有提醒（副本）"""
        with self.lock:
            return self.reminders.copy()

    def reset_notified_status(self, index: int):
        """重置提醒状态"""
        with self.lock:
            if 0 <= index < len(self.reminders):
                self.reminders[index].notified_days = []
                self.reminders[index].last_notify_time = None
                self.reminders[index].last_notify_date = None
                self.save_reminders()
                logger.info(f"重置提醒状态: {self.reminders[index].name}")

    def get_advance_days(self, reminder: Reminder) -> int:
        """获取提醒提前天数"""
        if reminder.advance_days is not None:
            return reminder.advance_days
        return self.config.get("default_advance_days", 1)

    def check_and_refresh_if_needed(self) -> bool:
        """检查是否需要刷新（每天0点后自动刷新）"""
        today = datetime.now().date()
        if today != self.last_refresh_date:
            self.last_refresh_date = today
            return True
        return False

    def perform_daily_refresh(self):
        """执行每日刷新操作"""
        with self.lock:
            self.reminders = self._load_reminders()
            self.last_refresh_date = datetime.now().date()
            logger.info("每日自动刷新完成")
            return True

    def export_data(self, filepath: str):
        """导出数据到文件"""
        with self.lock:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump([r.to_dict() for r in self.reminders], f, ensure_ascii=False, indent=2)
                logger.info(f"数据已导出到: {filepath}")
                return True
            except Exception as e:
                logger.error(f"导出数据失败: {e}")
                return False

    def import_data(self, filepath: str) -> bool:
        """从文件导入数据"""
        with self.lock:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    imported = [Reminder.from_dict(item) for item in data]
                    self.reminders.extend(imported)
                    self.save_reminders()
                    logger.info(f"从 {filepath} 导入了 {len(imported)} 个提醒")
                    return True
            except Exception as e:
                logger.error(f"导入数据失败: {e}")
                return False


# ==================== 提醒检查器 ====================
class ReminderChecker:
    """提醒检查器 - 负责检查到期提醒和发送通知"""

    def __init__(self, data_manager: DataManager):
        self.data_manager = data_manager
        self.running = True
        self.stop_event = threading.Event()
        self.logger = logging.getLogger(__name__)

    def check_expiry(self) -> List[str]:
        """检查快到期的项目，返回消息列表"""
        messages = []
        today = datetime.now().date()
        current_time = time.time()
        current_hour = int(current_time // 3600)
        reminders = self.data_manager.get_all_reminders()
        need_save = False

        for reminder in reminders:
            try:
                expiry_date = datetime.strptime(reminder.expiry_date, "%Y-%m-%d").date()
                days_left = (expiry_date - today).days
                advance_days = self.data_manager.get_advance_days(reminder)

                need_notify_by_days = False
                notify_days = []

                # 检查是否需要提醒
                if days_left >= 0:
                    for d in range(1, advance_days + 1):
                        if days_left == d and d not in reminder.notified_days:
                            need_notify_by_days = True
                            notify_days.append(d)

                    if days_left == 0 and 0 not in reminder.notified_days:
                        need_notify_by_days = True
                        notify_days.append(0)

                elif days_left < 0:
                    last_notify_date = reminder.last_notify_date
                    if last_notify_date != str(today):
                        need_notify_by_days = True
                        notify_days.append(-1)

                # 检查是否应该发送（频率控制）
                should_send = False
                if need_notify_by_days:
                    freq = self.data_manager.config.get("notify_frequency", "hourly")
                    if freq == "hourly":
                        if self._should_notify_hourly(reminder, current_hour):
                            should_send = True
                    else:  # daily
                        if self._should_notify_daily(reminder, str(today)):
                            should_send = True

                if should_send:
                    platform_info = f" [{reminder.platform}]" if reminder.platform else ""

                    if days_left < 0:
                        msg = f"⚠️ 警告：{reminder.name}{platform_info} 已在 {reminder.expiry_date} 过期！"
                        reminder.last_notify_time = current_time
                        reminder.last_notify_date = str(today)
                        if -1 not in reminder.notified_days:
                            reminder.notified_days.append(-1)
                    else:
                        if days_left == 0:
                            msg = f"📅 提醒：{reminder.name}{platform_info} 今天到期！"
                        else:
                            msg = f"📅 提醒：{reminder.name}{platform_info} 将在 {reminder.expiry_date} 到期（还剩{days_left}天）"

                        reminder.last_notify_time = current_time
                        reminder.last_notify_date = str(today)
                        for d in notify_days:
                            if d not in reminder.notified_days:
                                reminder.notified_days.append(d)

                    messages.append(msg)
                    need_save = True

            except Exception as e:
                self.logger.error(f"检查提醒出错: {e}")
                if "日期格式不正确" in str(e) or "time data" in str(e):
                    msg = f"❌ 错误：{reminder.name} 的日期格式不正确"
                    if msg not in messages:
                        messages.append(msg)

        if need_save:
            self.data_manager.save_reminders()

        return messages

    def _should_notify_hourly(self, reminder: Reminder, current_hour: int) -> bool:
        """检查是否应该每小时提醒一次"""
        if reminder.last_notify_time is None:
            return True
        last_hour = int(reminder.last_notify_time // 3600)
        return current_hour != last_hour

    def _should_notify_daily(self, reminder: Reminder, today: str) -> bool:
        """检查是否应该每天提醒一次"""
        if reminder.last_notify_date is None:
            return True
        return reminder.last_notify_date != today

    def show_notification(self, messages: List[str]):
        """显示系统弹窗"""
        if messages and self.data_manager.config.get("enable_notification", True):
            message_text = "\n".join(messages)
            try:
                notification.notify(
                    title="到期提醒工具",
                    message=message_text,
                    timeout=10
                )
                self.logger.info(f"发送通知: {len(messages)} 条")
            except Exception as e:
                self.logger.error(f"通知失败: {e}")
                # 降级到Tkinter消息框
                try:
                    root = Tk()
                    root.withdraw()
                    messagebox.showwarning("到期提醒", message_text)
                    root.destroy()
                except:
                    pass

    def run_background_check(self):
        """后台运行检查"""
        self.logger.info("后台检查线程启动")
        check_interval = self.data_manager.config.get("check_interval", 3600)

        while not self.stop_event.is_set():
            try:
                messages = self.check_expiry()
                if messages and self.data_manager.config.get("enable_notification", True):
                    self.show_notification(messages)

                # 使用事件等待，可以安全中断
                if self.stop_event.wait(check_interval):
                    break
            except Exception as e:
                self.logger.error(f"后台检查出错: {e}")
                if self.stop_event.wait(60):
                    break

        self.logger.info("后台检查线程停止")

    def stop(self):
        """停止后台检查"""
        self.logger.info("正在停止后台检查...")
        self.stop_event.set()


# ==================== 开机自启管理器 ====================
class AutoStartManager:
    """开机自启管理器"""

    def __init__(self):
        self.app_name = Constants.APP_NAME
        self.app_path = sys.executable if getattr(sys, 'frozen', False) else __file__
        self.logger = logging.getLogger(__name__)

    def is_auto_start_enabled(self) -> bool:
        """检查是否已启用开机自启"""
        try:
            if sys.platform == "win32":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_READ)
                try:
                    value, _ = winreg.QueryValueEx(key, self.app_name)
                    winreg.CloseKey(key)
                    return value == self.get_startup_command()
                except WindowsError:
                    return False
        except Exception as e:
            self.logger.error(f"检查开机自启状态失败: {e}")
            return False
        return False

    def get_startup_command(self) -> str:
        """获取启动命令"""
        if getattr(sys, 'frozen', False):
            return f'"{self.app_path}"'
        else:
            python_path = sys.executable
            return f'"{python_path}" "{self.app_path}"'

    def enable_auto_start(self) -> bool:
        """启用开机自启"""
        try:
            if sys.platform == "win32":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, self.app_name, 0, winreg.REG_SZ, self.get_startup_command())
                winreg.CloseKey(key)
                self.logger.info("已启用开机自启")
                return True
        except Exception as e:
            self.logger.error(f"启用开机自启失败: {e}")
            return False
        return False

    def disable_auto_start(self) -> bool:
        """禁用开机自启"""
        try:
            if sys.platform == "win32":
                import winreg
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run",
                                     0, winreg.KEY_SET_VALUE)
                try:
                    winreg.DeleteValue(key, self.app_name)
                except WindowsError:
                    pass
                winreg.CloseKey(key)
                self.logger.info("已禁用开机自启")
                return True
        except Exception as e:
            self.logger.error(f"禁用开机自启失败: {e}")
            return False
        return False


# ==================== 系统托盘图标 ====================
class SystemTrayIcon:
    """系统托盘图标"""

    def __init__(self, app):
        self.app = app
        self.icon = None
        self.running = True
        self.logger = logging.getLogger(__name__)
        self._create_tray_icon()

    def _create_tray_icon(self):
        """创建托盘图标"""
        image = self._create_image()

        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self.show_window, default=True),
            pystray.MenuItem("🔍立即检查", self.check_now),
            pystray.MenuItem("📋导入数据", self.import_data),
            pystray.MenuItem("📤导出数据", self.export_data),
            pystray.MenuItem("🚪退出程序", self.quit_app)
        )

        self.icon = pystray.Icon(
            "expiry_reminder",
            image,
            f"📅到期提醒工具 v{Constants.APP_VERSION}",
            menu,
            on_click=self._on_icon_click
        )

        threading.Thread(target=self.icon.run, daemon=True).start()
        self.logger.info("托盘图标已启动")

    def _create_image(self):
        """创建图标图像"""
        width, height = 64, 64
        image = Image.new('RGBA', (width, height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        # 绘制时钟图标
        draw.ellipse([(4, 4), (60, 60)], outline=(41, 128, 185), width=2)
        draw.ellipse([(6, 6), (58, 58)], fill=(247, 237, 188, 1))

        cx, cy = 32, 32
        draw.line([(cx, cy), (32, 16)], fill=(231, 76, 60), width=3)
        draw.line([(cx, cy), (48, 32)], fill=(231, 76, 60), width=2)
        draw.ellipse([(29, 29), (35, 35)], fill=(231, 76, 60))

        return image.convert('RGB')

    def _on_icon_click(self, icon, item):
        """点击图标事件"""
        if item is None:
            self.show_window()

    def show_window(self):
        """显示主窗口"""
        if self.app and self.app.root:
            self.app.root.after(0, self._show_window_impl)

    def _show_window_impl(self):
        """实际显示窗口"""
        try:
            if self.app and self.app.root:
                self.app.root.deiconify()
                self.app.root.lift()
                self.app.root.focus_force()
                self.app.root.attributes('-topmost', True)
                self.app.root.attributes('-topmost', False)
                self.app.window_visible = True
        except Exception as e:
            self.logger.error(f"显示窗口出错: {e}")

    def check_now(self):
        """立即检查"""
        if self.app:
            self.app.root.after(0, self.app.check_now)

    def import_data(self):
        """导入数据"""
        if self.app:
            self.app.root.after(0, self.app.import_data)

    def export_data(self):
        """导出数据"""
        if self.app:
            self.app.root.after(0, self.app.export_data)

    def quit_app(self):
        """退出程序"""
        if self.app:
            self.app.quit_app_directly()

    def stop(self):
        """停止托盘图标"""
        self.running = False
        if self.icon:
            try:
                self.icon.stop()
            except:
                pass
            self.icon = None
            self.logger.info("托盘图标已停止")


# ==================== 对话框类 ====================
class SettingsDialog:
    """设置对话框"""

    def __init__(self, parent, data_manager: DataManager, auto_start_manager: AutoStartManager, callback):
        self.parent = parent
        self.data_manager = data_manager
        self.auto_start_manager = auto_start_manager
        self.callback = callback
        self.logger = logging.getLogger(__name__)

        self.dialog = Toplevel(parent)
        self.dialog.title("设置")
        self.dialog.geometry("450x700")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._center_dialog()

        self.config = data_manager.config.copy()
        self._setup_ui()
        self.dialog.wait_window()

    def _center_dialog(self):
        """居中显示"""
        self.dialog.update_idletasks()
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()

        width, height = 450, 700
        x = parent_x + (parent_width - width) // 2
        y = parent_y + (parent_height - height) // 2
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_ui(self):
        """设置UI"""
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill="both", expand=True)

        # 滚动区域
        canvas = Canvas(main_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(main_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        row = 0
        # 默认提前提醒天数
        ttk.Label(scrollable_frame, text="默认提前提醒天数：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1
        ttk.Label(scrollable_frame, text="设置新提醒的默认提前天数", foreground="gray") \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10), padx=10)
        row += 1

        days_frame = ttk.Frame(scrollable_frame)
        days_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 20), padx=10)
        row += 1

        self.advance_days_var = IntVar(value=self.config["default_advance_days"])
        days_spinbox = Spinbox(days_frame, from_=0, to=30, textvariable=self.advance_days_var,
                               width=10, font=("Microsoft YaHei", 10))
        days_spinbox.pack(side="left", padx=(0, 10))
        ttk.Label(days_frame, text="天").pack(side="left")

        # 提醒说明
        info_frame = ttk.LabelFrame(scrollable_frame, text="提醒规则说明", padding=10)
        info_frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(0, 20), padx=10)
        row += 1

        info_text = """
• 设置为 0 天：只在到期当天提醒
• 设置为 1 天：提前1天和到期当天提醒
• 设置为 3 天：提前3、2、1天和到期当天提醒
• 设置为 7 天：提前7、6...1天和到期当天提醒
        """
        ttk.Label(info_frame, text=info_text, justify="left", foreground="gray").pack()

        # 提醒频率
        ttk.Label(scrollable_frame, text="提醒频率设置：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1
        ttk.Label(scrollable_frame, text="设置到期提醒的发送频率", foreground="gray") \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10), padx=10)
        row += 1

        frequency_frame = ttk.Frame(scrollable_frame)
        frequency_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 20), padx=10)
        row += 1

        self.notify_frequency_var = StringVar(value=self.config.get("notify_frequency", "hourly"))
        ttk.Radiobutton(frequency_frame, text="每小时提醒一次（频繁提醒）",
                        variable=self.notify_frequency_var, value="hourly").pack(anchor="w", pady=2)
        ttk.Radiobutton(frequency_frame, text="每天提醒一次（安静模式）",
                        variable=self.notify_frequency_var, value="daily").pack(anchor="w", pady=2)

        # 开机自启
        ttk.Label(scrollable_frame, text="开机自启设置：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1
        ttk.Label(scrollable_frame, text="设置程序是否随系统启动", foreground="gray") \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10), padx=10)
        row += 1

        startup_frame = ttk.Frame(scrollable_frame)
        startup_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 20), padx=10)
        row += 1

        self.auto_start_var = BooleanVar(value=self.auto_start_manager.is_auto_start_enabled())
        ttk.Checkbutton(startup_frame, text="开机自动启动程序",
                        variable=self.auto_start_var,
                        command=self._toggle_auto_start).pack(anchor="w")

        # 关闭行为
        ttk.Label(scrollable_frame, text="关闭程序时的行为：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1
        ttk.Label(scrollable_frame, text="点击关闭按钮时的默认操作", foreground="gray") \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10), padx=10)
        row += 1

        close_frame = ttk.Frame(scrollable_frame)
        close_frame.grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 10), padx=10)
        row += 1

        self.close_action_var = StringVar(value=self.config.get("close_action", "ask"))
        ttk.Radiobutton(close_frame, text="每次询问", variable=self.close_action_var,
                        value="ask").pack(anchor="w", pady=2)
        ttk.Radiobutton(close_frame, text="最小化到系统托盘", variable=self.close_action_var,
                        value="minimize").pack(anchor="w", pady=2)
        ttk.Radiobutton(close_frame, text="直接退出程序", variable=self.close_action_var,
                        value="exit").pack(anchor="w", pady=2)

        # 备份设置
        ttk.Label(scrollable_frame, text="数据备份设置：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1

        self.backup_var = BooleanVar(value=self.config.get("backup_enabled", True))
        ttk.Checkbutton(scrollable_frame, text="启用自动备份", variable=self.backup_var) \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 5), padx=10)
        row += 1

        # 通知开关
        ttk.Label(scrollable_frame, text="通知设置：", font=("Microsoft YaHei", 10, "bold")) \
            .grid(row=row, column=0, sticky="w", pady=(0, 5), padx=10)
        row += 1

        self.notification_var = BooleanVar(value=self.config["enable_notification"])
        ttk.Checkbutton(scrollable_frame, text="启用桌面通知", variable=self.notification_var) \
            .grid(row=row, column=0, columnspan=2, sticky="w", pady=(0, 20), padx=10)
        row += 1

        # 按钮
        button_frame = ttk.Frame(scrollable_frame)
        button_frame.grid(row=row, column=0, columnspan=2, pady=20)
        row += 1

        ttk.Button(button_frame, text="保存", command=self._save).pack(side="left", padx=10)
        ttk.Button(button_frame, text="取消", command=self._cancel).pack(side="left", padx=10)

    def _toggle_auto_start(self):
        """切换开机自启"""
        if self.auto_start_var.get():
            if self.auto_start_manager.enable_auto_start():
                messagebox.showinfo("成功", "✅已启用开机自启")
            else:
                messagebox.showerror("错误", "❌启用开机自启失败")
                self.auto_start_var.set(False)
        else:
            if self.auto_start_manager.disable_auto_start():
                messagebox.showinfo("成功", "✅已禁用开机自启")
            else:
                messagebox.showerror("错误", "❌禁用开机自启失败")
                self.auto_start_var.set(True)

    def _save(self):
        """保存设置"""
        self.config["default_advance_days"] = self.advance_days_var.get()
        self.config["enable_notification"] = self.notification_var.get()
        self.config["close_action"] = self.close_action_var.get()
        self.config["auto_start"] = self.auto_start_var.get()
        self.config["notify_frequency"] = self.notify_frequency_var.get()
        self.config["backup_enabled"] = self.backup_var.get()

        self.callback(self.config)
        self.dialog.destroy()

    def _cancel(self):
        self.dialog.destroy()


class EditDialog:
    """编辑提醒对话框"""

    def __init__(self, parent, title, reminder: Reminder = None, default_advance: int = 1):
        self.parent = parent
        self.reminder = reminder
        self.default_advance = default_advance
        self.result = None
        self.logger = logging.getLogger(__name__)

        self.dialog = Toplevel(parent)
        self.dialog.title(title)
        self.dialog.geometry("450x450")
        self.dialog.resizable(False, False)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self._center_dialog()

        self._setup_ui()
        self.dialog.wait_window()

    def _center_dialog(self):
        """居中显示"""
        self.dialog.update_idletasks()
        parent_x = self.parent.winfo_x()
        parent_y = self.parent.winfo_y()
        parent_width = self.parent.winfo_width()
        parent_height = self.parent.winfo_height()

        width, height = 450, 450
        x = parent_x + (parent_width - width) // 2
        y = parent_y + (parent_height - height) // 2
        self.dialog.geometry(f"{width}x{height}+{x}+{y}")

    def _setup_ui(self):
        """设置UI"""
        main_frame = ttk.Frame(self.dialog, padding=20)
        main_frame.pack(fill="both", expand=True)

        # 名称
        name_frame = ttk.Frame(main_frame)
        name_frame.pack(fill="x", pady=5)
        ttk.Label(name_frame, text="名称：", width=12).pack(side="left")
        self.name_var = StringVar(value=self.reminder.name if self.reminder else "")
        self.name_entry = ttk.Entry(name_frame, textvariable=self.name_var, width=35)
        self.name_entry.pack(side="left", padx=5, fill="x", expand=True)

        # 平台
        platform_frame = ttk.Frame(main_frame)
        platform_frame.pack(fill="x", pady=5)
        ttk.Label(platform_frame, text="使用平台：", width=12).pack(side="left")
        self.platform_var = StringVar(value=self.reminder.platform if self.reminder else "")
        self.platform_entry = ttk.Entry(platform_frame, textvariable=self.platform_var, width=35)
        self.platform_entry.pack(side="left", padx=5, fill="x", expand=True)

        # 日期
        date_frame = ttk.Frame(main_frame)
        date_frame.pack(fill="x", pady=5)
        ttk.Label(date_frame, text="到期时间：", width=12).pack(side="left")

        date_input_frame = ttk.Frame(date_frame)
        date_input_frame.pack(side="left", padx=5, fill="x", expand=True)

        if HAS_CALENDAR:
            if self.reminder:
                try:
                    initial_date = datetime.strptime(self.reminder.expiry_date, "%Y-%m-%d").date()
                except:
                    initial_date = datetime.now().date()
            else:
                initial_date = datetime.now().date()

            self.date_picker = DateEntry(date_input_frame, width=25, background='darkblue',
                                         foreground='white', borderwidth=2,
                                         year=initial_date.year, month=initial_date.month,
                                         day=initial_date.day, date_pattern='yyyy-mm-dd')
            self.date_picker.pack(side="left", fill="x", expand=True)
        else:
            self.date_var = StringVar(value=self.reminder.expiry_date if self.reminder else "")
            self.date_entry = ttk.Entry(date_input_frame, textvariable=self.date_var, width=30)
            self.date_entry.pack(side="left", fill="x", expand=True)
            ttk.Label(date_input_frame, text="(YYYY-MM-DD)", foreground="gray").pack(side="left", padx=5)

        # 提前提醒天数
        advance_frame = ttk.Frame(main_frame)
        advance_frame.pack(fill="x", pady=10)
        ttk.Label(advance_frame, text="提前提醒天数：", width=12).pack(side="left")

        advance_option_frame = ttk.Frame(advance_frame)
        advance_option_frame.pack(side="left", padx=5, fill="x", expand=True)

        self.advance_type = StringVar(
            value="custom" if self.reminder and self.reminder.advance_days is not None else "default")

        ttk.Radiobutton(advance_option_frame, text="使用默认设置", variable=self.advance_type,
                        value="default").pack(anchor="w")

        custom_frame = ttk.Frame(advance_option_frame)
        custom_frame.pack(anchor="w", pady=5)
        ttk.Radiobutton(custom_frame, text="自定义", variable=self.advance_type,
                        value="custom").pack(side="left")

        advance_value = self.reminder.advance_days if self.reminder and self.reminder.advance_days is not None else self.default_advance
        self.advance_days_var = IntVar(value=advance_value)
        self.advance_spinbox = Spinbox(custom_frame, from_=0, to=30, textvariable=self.advance_days_var,
                                       width=8,
                                       state="normal" if self.reminder and self.reminder.advance_days is not None else "disabled")
        self.advance_spinbox.pack(side="left", padx=10)
        ttk.Label(custom_frame, text="天").pack(side="left")

        self.advance_type.trace('w', lambda *args: self._toggle_advance_spinbox())

        # 快捷日期按钮
        quick_frame = ttk.LabelFrame(main_frame, text="📆快捷日期", padding=5)
        quick_frame.pack(fill="x", pady=10)

        quick_buttons_frame = ttk.Frame(quick_frame)
        quick_buttons_frame.pack()

        quick_dates = [
            ("明天", 1), ("后天", 2), ("三天后", 3),
            ("一周后", 7), ("两周后", 14), ("一个月后", 30),
            ("三个月后", 90), ("半年后", 182), ("一年后", 365)
        ]

        for i, (text, days) in enumerate(quick_dates):
            row, col = i // 3, i % 3
            btn = ttk.Button(quick_buttons_frame, text=text, width=10,
                             command=lambda d=days: self._set_quick_date(d))
            btn.grid(row=row, column=col, padx=3, pady=2)

        # 按钮
        button_frame = ttk.Frame(main_frame)
        button_frame.pack(pady=20)

        ttk.Button(button_frame, text="确定", command=self._ok, width=12).pack(side="left", padx=10)
        ttk.Button(button_frame, text="取消", command=self._cancel, width=12).pack(side="left", padx=10)

        self.name_entry.focus()
        self.dialog.bind('<Return>', lambda e: self._ok())
        self.dialog.bind('<Escape>', lambda e: self._cancel())

    def _toggle_advance_spinbox(self):
        """切换自定义提醒天数输入框状态"""
        if self.advance_type.get() == "custom":
            self.advance_spinbox.config(state="normal")
        else:
            self.advance_spinbox.config(state="disabled")

    def _set_quick_date(self, days):
        """设置快捷日期"""
        new_date = datetime.now() + timedelta(days=days)
        if HAS_CALENDAR:
            self.date_picker.set_date(new_date)
        else:
            self.date_var.set(new_date.strftime("%Y-%m-%d"))

    def _ok(self):
        """确定"""
        name = self.name_var.get().strip()
        platform = self.platform_var.get().strip()

        if not name:
            messagebox.showwarning("警告", "请填写名称", parent=self.dialog)
            return

        if HAS_CALENDAR:
            date_str = self.date_picker.get()
        else:
            date_str = self.date_var.get().strip()
            if not date_str:
                messagebox.showwarning("警告", "请填写到期时间", parent=self.dialog)
                return

        try:
            datetime.strptime(date_str, "%Y-%m-%d")

            if self.advance_type.get() == "default":
                advance_days = None
            else:
                advance_days = self.advance_days_var.get()

            self.result = (name, platform, date_str, advance_days)
            self.dialog.destroy()
        except ValueError:
            messagebox.showerror("错误", "日期格式错误，请使用 YYYY-MM-DD 格式", parent=self.dialog)

    def _cancel(self):
        self.dialog.destroy()


# ==================== 主界面 ====================
class ReminderGUI:
    def __init__(self):
        self.data_manager = DataManager()
        self.auto_start_manager = AutoStartManager()
        self.checker = ReminderChecker(self.data_manager)
        self.logger = logging.getLogger(__name__)

        self.root = Tk()
        self.root.title(f"📅到期提醒工具 v{Constants.APP_VERSION}")
        self.root.geometry("1050x750")
        self.root.minsize(950, 650)
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

        self.window_visible = True
        self._setup_ui()
        self._refresh_list()
        self._setup_context_menu()

        # 启动托盘图标
        self.tray_icon = SystemTrayIcon(self)

        # 同步开机自启配置
        self._sync_auto_start_config()

        # 启动每日刷新检查
        self._start_daily_refresh_checker()

        # 启动后台检查线程
        self.bg_thread = threading.Thread(target=self.checker.run_background_check, daemon=True)
        self.bg_thread.start()

    def _setup_ui(self):
        """设置界面"""
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill="both", expand=True)

        # 菜单栏
        menubar = Menu(self.root)
        self.root.config(menu=menubar)

        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="文件", menu=file_menu)
        file_menu.add_command(label="🔍立即检查", command=self.check_now)
        file_menu.add_separator()
        file_menu.add_command(label="📤导出数据", command=self.export_data)
        file_menu.add_command(label="📋导入数据", command=self.import_data)
        file_menu.add_separator()
        file_menu.add_command(label="最小化到托盘", command=self._hide_window)
        file_menu.add_separator()
        file_menu.add_command(label="🚪退出", command=self.quit_app_directly)

        settings_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="设置", menu=settings_menu)
        settings_menu.add_command(label="程序设置", command=self.open_settings)
        settings_menu.add_separator()
        settings_menu.add_command(label="重置关闭行为", command=self._reset_close_action)
        settings_menu.add_separator()
        settings_menu.add_command(label="ℹ️关于", command=self._show_about)

        # 左右分栏
        left_frame = ttk.Frame(main_frame, width=380)
        left_frame.pack(side="left", fill="both", expand=False, padx=(0, 10))
        left_frame.pack_propagate(False)

        right_frame = ttk.Frame(main_frame)
        right_frame.pack(side="right", fill="both", expand=True)

        # 左侧：添加表单
        self._setup_add_panel(left_frame)

        # 右侧：列表
        self._setup_list_panel(right_frame)

    def _setup_add_panel(self, parent):
        """设置添加面板"""
        add_frame = ttk.LabelFrame(parent, text="📝添加新提醒", padding=15)
        add_frame.pack(fill="both", expand=True)

        # 名称
        name_label = ttk.Label(add_frame, text="名称：", font=("Microsoft YaHei", 10))
        name_label.grid(row=0, column=0, sticky="w", pady=8)
        self.name_var = StringVar()
        self.name_entry = ttk.Entry(add_frame, textvariable=self.name_var, width=30, font=("Microsoft YaHei", 10))
        self.name_entry.grid(row=0, column=1, padx=10, pady=8, sticky="ew")

        # 平台
        platform_label = ttk.Label(add_frame, text="💻使用平台：", font=("Microsoft YaHei", 10))
        platform_label.grid(row=1, column=0, sticky="w", pady=8)
        self.platform_var = StringVar()
        self.platform_entry = ttk.Entry(add_frame, textvariable=self.platform_var, width=30,
                                        font=("Microsoft YaHei", 10))
        self.platform_entry.grid(row=1, column=1, padx=10, pady=8, sticky="ew")

        # 日期
        date_label = ttk.Label(add_frame, text="📅到期时间：", font=("Microsoft YaHei", 10))
        date_label.grid(row=2, column=0, sticky="w", pady=8)

        date_frame = ttk.Frame(add_frame)
        date_frame.grid(row=2, column=1, padx=10, pady=8, sticky="ew")

        if HAS_CALENDAR:
            self.date_picker = DateEntry(date_frame, width=28, background='darkblue',
                                         foreground='white', borderwidth=2,
                                         year=datetime.now().year,
                                         month=datetime.now().month,
                                         day=datetime.now().day,
                                         date_pattern='yyyy-mm-dd')
            self.date_picker.pack(side="left", fill="x", expand=True)
        else:
            self.date_var = StringVar()
            self.date_entry = ttk.Entry(date_frame, textvariable=self.date_var, width=30)
            self.date_entry.pack(side="left", fill="x", expand=True)
            ttk.Label(date_frame, text="(YYYY-MM-DD)", foreground="gray").pack(side="left", padx=5)

        # 提前提醒
        advance_label = ttk.Label(add_frame, text="⏰提前提醒：", font=("Microsoft YaHei", 10))
        advance_label.grid(row=3, column=0, sticky="w", pady=8)

        advance_frame = ttk.Frame(add_frame)
        advance_frame.grid(row=3, column=1, padx=10, pady=8, sticky="w")

        self.use_default_advance = BooleanVar(value=True)
        default_check = ttk.Checkbutton(advance_frame, text="使用默认设置",
                                        variable=self.use_default_advance,
                                        command=self._toggle_advance_input)
        default_check.grid(row=0, column=0, sticky="w")

        custom_frame = ttk.Frame(advance_frame)
        custom_frame.grid(row=1, column=0, sticky="w", pady=5)

        ttk.Label(custom_frame, text="自定义：").pack(side="left")
        self.custom_advance_var = IntVar(value=self.data_manager.config["default_advance_days"])
        self.advance_spinbox = Spinbox(custom_frame, from_=0, to=30,
                                       textvariable=self.custom_advance_var,
                                       width=8, state="disabled")
        self.advance_spinbox.pack(side="left", padx=5)
        ttk.Label(custom_frame, text="天").pack(side="left")

        # 快捷日期
        quick_frame = ttk.LabelFrame(add_frame, text="⚡快捷日期", padding=5)
        quick_frame.grid(row=4, column=0, columnspan=2, sticky="ew", pady=15)

        quick_buttons = [
            ("明天", 1), ("后天", 2), ("三天后", 3),
            ("一周后", 7), ("两周后", 14), ("一个月后", 30),
            ("三个月后", 90), ("半年后", 182), ("一年后", 365)
        ]

        for i, (text, days) in enumerate(quick_buttons):
            row, col = i // 3, i % 3
            btn = ttk.Button(quick_frame, text=text, width=10,
                             command=lambda d=days: self._set_quick_date(d))
            btn.grid(row=row, column=col, padx=3, pady=2)

        # 添加按钮
        add_button = ttk.Button(add_frame, text="➕ 添加提醒", command=self._add_reminder, width=25)
        add_button.grid(row=5, column=0, columnspan=2, pady=20)

        add_frame.columnconfigure(1, weight=1)

    def _setup_list_panel(self, parent):
        """设置列表面板"""
        list_frame = ttk.LabelFrame(parent, text="📋提醒列表", padding=10)
        list_frame.pack(fill="both", expand=True)

        # 搜索框
        search_frame = ttk.Frame(list_frame)
        search_frame.pack(fill="x", pady=(0, 10))

        ttk.Label(search_frame, text="🔍 搜索：", font=("Microsoft YaHei", 9)).pack(side="left")
        self.search_var = StringVar()
        self.search_var.trace('w', lambda *args: self._refresh_list())
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=40)
        self.search_entry.pack(side="left", padx=5, fill="x", expand=True)

        # 树形列表
        tree_container = ttk.Frame(list_frame)
        tree_container.pack(fill="both", expand=True)

        scrollbar_y = ttk.Scrollbar(tree_container)
        scrollbar_y.pack(side="right", fill="y")

        scrollbar_x = ttk.Scrollbar(tree_container, orient="horizontal")
        scrollbar_x.pack(side="bottom", fill="x")

        columns = ("名称", "使用平台", "到期时间", "剩余天数", "状态", "提醒设置")
        self.tree = ttk.Treeview(tree_container, columns=columns, show="headings",
                                 yscrollcommand=scrollbar_y.set,
                                 xscrollcommand=scrollbar_x.set,
                                 height=18)

        column_widths = {"名称": 180, "使用平台": 140, "到期时间": 100, "剩余天数": 90, "状态": 90, "提醒设置": 90}
        for col, width in column_widths.items():
            self.tree.heading(col, text=col)
            self.tree.column(col, width=width, minwidth=width, anchor='center')

        self.tree.pack(fill="both", expand=True)
        scrollbar_y.config(command=self.tree.yview)
        scrollbar_x.config(command=self.tree.xview)

        self.tree.bind("<Double-Button-1>", self._edit_reminder)

        # 按钮工具栏
        button_toolbar = ttk.Frame(list_frame)
        button_toolbar.pack(fill="x", pady=10)

        button_row1 = ttk.Frame(button_toolbar)
        button_row1.pack(fill="x", pady=2)

        ttk.Button(button_row1, text="✏️ 编辑选中", command=self._edit_selected, width=14).pack(side="left", padx=2)
        ttk.Button(button_row1, text="🗑 删除选中", command=self._delete_reminder, width=14).pack(side="left", padx=2)
        ttk.Button(button_row1, text="🔄 立即检查", command=self.check_now, width=14).pack(side="left", padx=2)
        ttk.Button(button_row1, text="📋 刷新列表", command=self._refresh_list, width=14).pack(side="left", padx=2)

        button_row2 = ttk.Frame(button_toolbar)
        button_row2.pack(fill="x", pady=2)

        ttk.Button(button_row2, text="🔄 重置提醒状态", command=self._reset_notification, width=14).pack(side="left",
                                                                                                        padx=2)
        ttk.Button(button_row2, text="⚙️ 程序设置", command=self.open_settings, width=14).pack(side="left", padx=2)
        ttk.Button(button_row2, text="ℹ️ 关于", command=self._show_about, width=14).pack(side="left", padx=2)
        ttk.Button(button_row2, text="🚪 最小化到托盘", command=self._hide_window, width=14).pack(side="left", padx=2)

        # 状态栏
        status_frame = ttk.LabelFrame(parent, text="统计信息", padding=5)
        status_frame.pack(fill="x", pady=(5, 0))

        self.status_var = StringVar()
        self._update_status()

        status_label = ttk.Label(status_frame, textvariable=self.status_var,
                                 font=("Microsoft YaHei", 10),
                                 padding=5)
        status_label.pack(fill="x")

        close_action_text = self._get_close_action_text()
        tip_label = ttk.Label(status_frame,
                              text=f"💡 提示：双击列表项可编辑 | 最小化到任务栏 | 关闭时{close_action_text}",
                              foreground="gray", font=("Microsoft YaHei", 9),
                              padding=2)
        tip_label.pack(fill="x")

    def _toggle_advance_input(self):
        """切换提醒天数输入框状态"""
        if self.use_default_advance.get():
            self.advance_spinbox.config(state="disabled")
        else:
            self.advance_spinbox.config(state="normal")

    def _set_quick_date(self, days):
        """设置快捷日期"""
        new_date = datetime.now() + timedelta(days=days)
        if HAS_CALENDAR:
            self.date_picker.set_date(new_date)
        else:
            self.date_var.set(new_date.strftime("%Y-%m-%d"))

    def _setup_context_menu(self):
        """设置右键菜单"""
        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="✏️编辑", command=self._edit_selected)
        self.context_menu.add_command(label="🗑删除", command=self._delete_reminder)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="🔄重置提醒状态", command=self._reset_notification)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="📋刷新", command=self._refresh_list)

        self.tree.bind("<Button-3>", self._show_context_menu)

    def _show_context_menu(self, event):
        """显示右键菜单"""
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            self.context_menu.post(event.x_root, event.y_root)

    def _get_close_action_text(self) -> str:
        """获取关闭行为文本"""
        action = self.data_manager.config.get("close_action", "ask")
        if action == "ask":
            return "会询问操作"
        elif action == "minimize":
            return "会自动最小化到托盘"
        else:
            return "会直接退出程序"

    def _update_status(self):
        """更新状态栏"""
        reminders = self.data_manager.get_all_reminders()
        total_count = len(reminders)
        filtered_count = len(self.tree.get_children())
        default_days = self.data_manager.config.get('default_advance_days', 1)
        freq_text = "🕐每小时检查" if self.data_manager.config.get("notify_frequency") == "hourly" else "每天检查"
        last_refresh = self.data_manager.last_refresh_date.strftime("%Y-%m-%d")
        self.status_var.set(
            f"总计：{total_count} 项 | 显示：{filtered_count} 项 | 默认提前 {default_days} 天 | {freq_text} | 最后刷新：{last_refresh}"
        )

    def _sync_auto_start_config(self):
        """同步开机自启配置"""
        current_status = self.auto_start_manager.is_auto_start_enabled()
        if current_status != self.data_manager.config.get("auto_start", False):
            self.data_manager.config["auto_start"] = current_status
            self.data_manager.save_config()

    def _start_daily_refresh_checker(self):
        """启动每日刷新检查器"""

        def initial_refresh():
            try:
                self._refresh_list()
                reminders = self.data_manager.get_all_reminders()
                if reminders:
                    self.status_var.set(f"✓ 已加载 {len(reminders)} 项提醒数据")
                else:
                    self.status_var.set("暂无提醒数据")
            except Exception as e:
                self.logger.error(f"初始加载数据失败: {e}")

        def check_daily_refresh():
            try:
                if self.data_manager.check_and_refresh_if_needed():
                    self.data_manager.perform_daily_refresh()
                    self._refresh_list()
                    self.status_var.set(f"✓ 每日自动刷新完成 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
                    try:
                        notification.notify(
                            title="到期提醒工具",
                            message="提醒列表已自动刷新，数据已更新",
                            timeout=2
                        )
                    except:
                        pass
            except Exception as e:
                self.logger.error(f"每日刷新检查失败: {e}")
            # 每分钟检查一次
            self.root.after(60000, check_daily_refresh)

        self.root.after(300, initial_refresh)
        self.root.after(1000, check_daily_refresh)

    def _add_reminder(self):
        """添加提醒"""
        name = self.name_var.get().strip()
        platform = self.platform_var.get().strip()

        if not name:
            messagebox.showwarning("警告", "请填写名称")
            return

        if HAS_CALENDAR:
            date_str = self.date_picker.get()
        else:
            date_str = self.date_var.get().strip()
            if not date_str:
                messagebox.showwarning("警告", "请填写到期时间")
                return

        try:
            datetime.strptime(date_str, "%Y-%m-%d")

            if self.use_default_advance.get():
                advance_days = None
            else:
                advance_days = self.custom_advance_var.get()

            reminder = Reminder(name=name, platform=platform, expiry_date=date_str, advance_days=advance_days)
            self.data_manager.add_reminder(reminder)
            self._refresh_list()

            self.name_var.set("")
            self.platform_var.set("")
            if not HAS_CALENDAR:
                self.date_var.set("")

            self.status_var.set(f"✓ 已添加提醒：{name}")

            try:
                notification.notify(
                    title="📅到期提醒工具",
                    message=f"已添加提醒：{name}，到期时间：{date_str}",
                    timeout=3
                )
            except:
                pass
        except ValueError:
            messagebox.showerror("错误", "日期格式错误，请使用 YYYY-MM-DD 格式")

    def _edit_selected(self):
        """编辑选中的提醒"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中要编辑的提醒")
            return

        index = int(selection[0])
        reminder = self.data_manager.get_reminder(index)
        if not reminder:
            messagebox.showerror("错误", "提醒不存在")
            return

        dialog = EditDialog(self.root, "✏️编辑提醒", reminder,
                            self.data_manager.config["default_advance_days"])

        if dialog.result:
            name, platform, date_str, advance_days = dialog.result
            updated_reminder = Reminder(
                name=name,
                platform=platform,
                expiry_date=date_str,
                advance_days=advance_days,
                notified_days=reminder.notified_days,
                last_notify_time=reminder.last_notify_time,
                last_notify_date=reminder.last_notify_date
            )
            if self.data_manager.update_reminder(index, updated_reminder):
                self._refresh_list()
                self.status_var.set(f"✓ 已更新提醒：{name}")
            else:
                messagebox.showerror("错误", "更新失败")

    def _edit_reminder(self, event):
        """双击编辑"""
        self._edit_selected()

    def _delete_reminder(self):
        """删除选中的提醒"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中要删除的提醒")
            return

        if messagebox.askyesno("确认删除", "确定要删除选中的提醒吗？"):
            for item in selection:
                index = int(item)
                self.data_manager.delete_reminder(index)
            self._refresh_list()
            self.status_var.set("✓ 已删除提醒")

    def _reset_notification(self):
        """重置选中提醒的提醒状态"""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("提示", "请先选中要重置提醒状态的提醒")
            return

        if messagebox.askyesno("确认重置", "重置后将重新发送所有未到期的提醒，确定吗？"):
            for item in selection:
                index = int(item)
                self.data_manager.reset_notified_status(index)
            self._refresh_list()
            self.status_var.set("✓ 已重置提醒状态")

    def _get_days_left(self, expiry_date: str) -> Optional[int]:
        """计算剩余天数"""
        try:
            expiry = datetime.strptime(expiry_date, "%Y-%m-%d").date()
            today = datetime.now().date()
            return (expiry - today).days
        except:
            return None

    def _get_advance_display(self, reminder: Reminder) -> str:
        """获取提醒设置的显示文本"""
        if reminder.advance_days is not None:
            return f"提前 ({reminder.advance_days}天)"
        else:
            return f"提前 ({self.data_manager.config['default_advance_days']}天)"

    def _get_status_text(self, days_left: Optional[int], reminder: Reminder) -> str:
        """获取状态文本"""
        if days_left is None:
            return "❌日期错误"
        elif days_left < 0:
            return "⛔已过期"
        elif days_left == 0:
            return "🔴今天到期"
        elif days_left <= self.data_manager.get_advance_days(reminder):
            return "🟡即将到期"
        else:
            return "🟢正常"

    def _refresh_list(self):
        """刷新提醒列表"""
        for item in self.tree.get_children():
            self.tree.delete(item)

        search_keyword = self.search_var.get().strip().lower()
        reminders = self.data_manager.get_all_reminders()

        for i, reminder in enumerate(reminders):
            if search_keyword:
                if search_keyword not in reminder.name.lower() and search_keyword not in reminder.platform.lower():
                    continue

            days_left = self._get_days_left(reminder.expiry_date)
            status_text = self._get_status_text(days_left, reminder)
            advance_display = self._get_advance_display(reminder)

            if days_left is not None:
                if days_left < 0:
                    days_display = f"已过期{-days_left}天"
                else:
                    days_display = f"{days_left}天"
            else:
                days_display = "格式错误"

            item_id = self.tree.insert("", "end", iid=str(i), values=(
                reminder.name,
                reminder.platform,
                reminder.expiry_date,
                days_display,
                status_text,
                advance_display
            ))

            # 颜色标记
            tag = None
            if status_text in ["⛔已过期", "🔴今天到期"]:
                tag = 'expired'
            elif status_text == "🟡即将到期":
                tag = 'warning'

            if tag:
                self.tree.tag_configure(tag, foreground='red')
                if status_text == "🔴今天到期":
                    self.tree.tag_configure(tag, font=('Microsoft YaHei', 9, 'bold'))
                self.tree.item(item_id, tags=(tag,))

        self._update_status()

    def check_now(self):
        """立即检查"""
        messages = self.checker.check_expiry()
        if messages:
            self.checker.show_notification(messages)
            self.status_var.set(f"⚠️ 发现 {len(messages)} 个到期提醒")
        else:
            self.status_var.set("✓ 没有即将到期的项目")
        self._refresh_list()

    def export_data(self):
        """导出数据"""
        filepath = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            title="导出数据"
        )
        if filepath:
            if self.data_manager.export_data(filepath):
                messagebox.showinfo("成功", f"数据已导出到:\n{filepath}")
            else:
                messagebox.showerror("错误", "导出数据失败")

    def import_data(self):
        """导入数据"""
        filepath = filedialog.askopenfilename(
            filetypes=[("JSON文件", "*.json"), ("所有文件", "*.*")],
            title="导入数据"
        )
        if filepath:
            if messagebox.askyesno("确认导入", "导入将追加数据到当前列表，确定继续吗？"):
                if self.data_manager.import_data(filepath):
                    self._refresh_list()
                    messagebox.showinfo("成功", "数据导入成功")
                else:
                    messagebox.showerror("错误", "导入数据失败")

    def open_settings(self):
        """打开设置对话框"""
        SettingsDialog(self.root, self.data_manager, self.auto_start_manager, self._update_settings)

    def _update_settings(self, new_config):
        """更新设置"""
        self.data_manager.config = new_config
        self.data_manager.save_config()
        self._refresh_list()

        close_action_text = self._get_close_action_text()
        self._update_status()

        self.custom_advance_var.set(new_config["default_advance_days"])

    def _reset_close_action(self):
        """重置关闭行为到询问模式"""
        self.data_manager.config["close_action"] = "ask"
        self.data_manager.save_config()
        self.status_var.set("✅已重置关闭行为，下次关闭时会询问")

    def _show_about(self):
        """显示关于对话框 - 修复版"""
        auto_start_status = "✅已启用" if self.auto_start_manager.is_auto_start_enabled() else "❌已禁用"
        freq_text = "🕐每小时提醒" if self.data_manager.config.get("notify_frequency") == "hourly" else "📅每天提醒"

        about_text = f"""到期提醒工具 v{Constants.APP_VERSION}

✨功能特点：
• 自定义提前提醒天数（支持每个提醒单独设置）
• 支持记录使用平台信息（自由输入）
• 到期当天和提前多天提醒
• 灵活的提醒频率（每小时/每天）
• 系统托盘后台运行
• 日期选择器（支持手动输入）
• 搜索和筛选功能
• 颜色标记状态
• 开机自启动（可选）
• 数据自动备份和恢复
• 数据导入/导出

⚙️当前设置：
默认提前天数：{self.data_manager.config['default_advance_days']} 天
提醒频率：{freq_text}
通知状态：{'✅启用' if self.data_manager.config['enable_notification'] else '❌禁用'}
关闭行为：{self._get_close_action_text()}
开机自启：{auto_start_status}
数据备份：{'✅启用' if self.data_manager.config.get('backup_enabled', True) else '❌禁用'}

📖操作说明：
• 最小化按钮：最小化到任务栏
• 关闭按钮：根据设置决定（询问/最小化到托盘/退出）
• 系统托盘：可右键进行快捷操作

📁数据文件：reminders.json（自动备份到 reminders_backup.json）
📁配置文件：config.json
📁日志文件：expiry_reminder.log

💬QQ：664969123
© 2026 到期提醒工具
        """

        about_dialog = Toplevel(self.root)
        about_dialog.title("ℹ️关于")
        about_dialog.geometry("500x560")
        about_dialog.resizable(False, False)
        about_dialog.transient(self.root)
        about_dialog.grab_set()

        self._center_window(about_dialog, 500, 560)

        # 使用网格布局确保按钮可见
        about_dialog.rowconfigure(0, weight=1)  # 文本区占满剩余空间
        about_dialog.rowconfigure(1, weight=0)  # 按钮区固定
        about_dialog.columnconfigure(0, weight=1)

        # 文本区域 - 第0行
        text_frame = ttk.Frame(about_dialog, padding=(20, 20, 5, 10))
        text_frame.grid(row=0, column=0, sticky="nsew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        text_widget = Text(text_frame, wrap="word", font=("Microsoft YaHei", 10))
        scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
        text_widget.configure(yscrollcommand=scrollbar.set)

        text_widget.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        text_widget.insert("1.0", about_text)
        text_widget.config(state="disabled")

        # 按钮区域 - 第1行
        button_frame = ttk.Frame(about_dialog, padding=(0, 15, 0, 20))
        button_frame.grid(row=1, column=0, sticky="ew")

        btn_container = ttk.Frame(button_frame)
        btn_container.pack()
        ttk.Button(btn_container, text="确定", command=about_dialog.destroy, width=15).pack(pady=5)

        about_dialog.bind('<Escape>', lambda e: about_dialog.destroy())
        about_dialog.bind('<Return>', lambda e: about_dialog.destroy())

    def _show_close_dialog(self):
        """显示关闭选项对话框 - 修复版"""
        dialog = Toplevel(self.root)
        dialog.title("🚪退出选项")
        dialog.geometry("420x320")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()
        self._center_window(dialog, 420, 320)

        # 使用网格布局
        dialog.rowconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=0)
        dialog.columnconfigure(0, weight=1)

        # 主内容 - 第0行
        main_frame = ttk.Frame(dialog, padding=(20, 20, 20, 10))
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=1)

        # 标题
        title_frame = ttk.Frame(main_frame)
        title_frame.pack(fill="x", pady=(0, 5))
        ttk.Label(title_frame, text="退出选项",
                  font=("Microsoft YaHei", 11, "bold")).pack(side="left")
        ttk.Label(title_frame, text="请选择操作方式",
                  foreground="gray").pack(side="right")

        # 选项区域
        options_frame = ttk.LabelFrame(main_frame, text="操作选择", padding=15)
        options_frame.pack(fill="x", pady=(10, 10))

        self.close_choice = StringVar(value="minimize")

        radio1_frame = ttk.Frame(options_frame)
        radio1_frame.pack(fill="x", pady=5)
        ttk.Radiobutton(radio1_frame, text="最小化到系统托盘（后台运行）",
                        variable=self.close_choice, value="minimize").pack(side="left")

        radio2_frame = ttk.Frame(options_frame)
        radio2_frame.pack(fill="x", pady=5)
        ttk.Radiobutton(radio2_frame, text="完全退出程序（停止所有提醒）",
                        variable=self.close_choice, value="exit").pack(side="left")

        # 记住选项
        self.remember_choice = BooleanVar(value=False)
        remember_frame = ttk.Frame(main_frame)
        remember_frame.pack(fill="x", pady=(5, 5))
        ttk.Checkbutton(remember_frame, text="记住我的选择，下次不再询问",
                        variable=self.remember_choice).pack(anchor="w")

        # 提示
        tip_frame = ttk.Frame(main_frame)
        tip_frame.pack(fill="x", pady=(5, 10))
        ttk.Label(tip_frame, text="💡 勾选后将会保存选择，下次关闭时自动执行",
                  foreground="#666666", font=("Microsoft YaHei", 8)).pack(anchor="w")

        # 按钮区域 - 第1行
        button_frame = ttk.Frame(dialog, padding=(0, 5, 0, 20))
        button_frame.grid(row=1, column=0, sticky="ew")

        def on_confirm():
            choice = self.close_choice.get()
            if self.remember_choice.get():
                self.data_manager.config["close_action"] = choice
                self.data_manager.save_config()
            dialog.destroy()

            if choice == "minimize":
                self._hide_window()
            else:
                self.quit_app_directly()

        def on_cancel():
            dialog.destroy()

        btn_container = ttk.Frame(button_frame)
        btn_container.pack()
        ttk.Button(btn_container, text="确定", command=on_confirm, width=12).pack(side="left", padx=(0, 10))
        ttk.Button(btn_container, text="取消", command=on_cancel, width=12).pack(side="left")

        dialog.bind('<Return>', lambda e: on_confirm())
        dialog.bind('<Escape>', lambda e: on_cancel())

    def _center_window(self, window, width, height):
        """居中显示窗口"""
        window.update_idletasks()
        parent_x = self.root.winfo_x()
        parent_y = self.root.winfo_y()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()

        x = parent_x + (parent_width - width) // 2
        y = parent_y + (parent_height - height) // 2
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _on_closing(self):
        """处理窗口关闭事件"""
        action = self.data_manager.config.get("close_action", "ask")

        if action == "ask":
            self._show_close_dialog()
        elif action == "minimize":
            self._hide_window()
        elif action == "exit":
            self.quit_app_directly()

    def _hide_window(self):
        """隐藏窗口到系统托盘"""
        self.root.withdraw()
        self.window_visible = False
        try:
            notification.notify(
                title="📅到期提醒工具",
                message="程序已最小化到系统托盘，将继续运行",
                timeout=2
            )
        except:
            pass

    def quit_app_directly(self):
        """直接退出程序"""
        self.logger.info("正在退出程序...")

        # 停止后台检查
        if hasattr(self, 'checker'):
            self.checker.stop()

        # 停止托盘图标
        if hasattr(self, 'tray_icon'):
            self.tray_icon.stop()
            time.sleep(0.2)

        # 保存数据
        self.data_manager.save_reminders()
        self.data_manager.save_config()

        # 退出GUI
        self.root.quit()
        self.root.destroy()
        sys.exit(0)


# ==================== 主函数 ====================
def main():
    """主函数"""
    # 检查依赖
    missing_libs = []
    try:
        import plyer
    except ImportError:
        missing_libs.append("plyer")

    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        missing_libs.extend(["pystray", "Pillow"])

    if missing_libs:
        print(f"缺少必要的依赖库: {', '.join(missing_libs)}")
        print("正在安装必要的依赖库...")
        os.system("pip install plyer pystray Pillow")
        print("安装完成，请重新运行程序")
        sys.exit(0)

    if not HAS_CALENDAR:
        print("\n💡 提示：安装 tkcalendar 可以获得更好的日期选择体验")
        print("   运行：pip install tkcalendar\n")

    # 创建并运行应用
    app = ReminderGUI()
    app.root.mainloop()


if __name__ == "__main__":
    main()