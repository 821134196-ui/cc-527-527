"""公共楼道占用巡查与物品处置系统。

模块:
  db       -- SQLite 连接与建表
  storage  -- 本地图片存储
  images   -- 占位巡查照片生成 (无外部依赖)
  messages -- 模拟消息适配器
  services -- 领域逻辑 (巡查/通知/复查/封存/认领/处置)
  web      -- 本地 HTTP 界面
"""

__version__ = "1.0.0"
