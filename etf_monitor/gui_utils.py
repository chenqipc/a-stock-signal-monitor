import subprocess
import threading
import time

class NotificationManager:
    """
    处理GUI通知功能的类
    """
    
    def __init__(self):
        """初始化通知管理器"""
        self.active_notifications = []
        
    def show_notification(self, title, message, auto_close_seconds=120):
        """
        显示一个通知弹窗
        
        参数:
            title: 弹窗标题
            message: 弹窗内容
            auto_close_seconds: 自动关闭时间(秒)，默认2分钟
        """
        # 及时移除已经结束的线程，避免长期运行时列表无限增长。
        self.active_notifications = [thread for thread in self.active_notifications if thread.is_alive()]
        notification_thread = threading.Thread(
            target=self._show_notification_window,
            args=(title, message, auto_close_seconds)
        )
        notification_thread.daemon = True  # 设置为守护线程，这样主程序退出时线程也会退出
        notification_thread.start()
        
        # 将线程添加到活动通知列表中
        self.active_notifications.append(notification_thread)
        
    def _show_notification_window(self, title, message, auto_close_seconds):
        """
        内部方法：显示通知窗口并在指定时间后自动关闭
        使用macOS的原生对话框
        """
        # AppleScript 字符串必须转义，避免证券名称中的引号破坏脚本。
        safe_title = self._escape_applescript(title)
        safe_message = self._escape_applescript(message)
        applescript = f'''
        display dialog "{safe_message}" with title "{safe_title}" buttons {{"确定"}} default button "确定" giving up after {auto_close_seconds}
        '''
        
        try:
            subprocess.run(["osascript", "-e", applescript], timeout=auto_close_seconds + 5, check=False)
        except Exception as e:
            print(f"显示通知失败: {e}")

    @staticmethod
    def _escape_applescript(value):
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

# 使用示例
if __name__ == "__main__":
    notifier = NotificationManager()
    notifier.show_notification(
        "512980", 
        "512980 在 15min线 周期当前价格持续在10日均线上方",
        30  # 30秒后自动关闭
    )
    
    # 保持主程序运行，以便看到通知效果
    print("通知已发送，主程序继续运行...")
    time.sleep(60)  # 主程序等待60秒后退出
