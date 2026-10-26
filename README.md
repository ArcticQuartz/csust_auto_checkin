# csust_auto_checkin
长沙理工大学研究生自动打卡脚本

###该方法需要一部手机常连接电脑，可以用备用机登录，也可以使用AI修改此脚本，使其检测到手机开机或其他情况下使用
使用方法：
1. 在dingtalk_checkin.py中，将 CHAT_NAME 变量修改为自己所在学院的打卡群聊名称。
2. 在dingtalk_checkin.py中，将 ADB_CANDIDATES 变量的值修改为本机电脑上的adb路径（我用的是自己mumu模拟器的adb，没有的可以自己下一个）
3. 在register_checkin_task.ps1中，将 param() 中的 [string]$Python 值修改为你电脑中python的路径；[string]$At 值修改为你想要进行打卡的时间

## 一、手机侧一次性设置

| 项目 | 位置 | 说明 |
|---|---|---|
| USB 调试 | 设置 → 系统和更新 → 开发人员选项 | 打开 |
| 仅充电模式下允许 ADB 调试 | 同上 | **华为必开** |
| 锁屏密码 | 设置 → 生物识别和密码 → 锁屏密码 → 关闭 | **必须**，否则点击落在锁屏上 |
| 定位服务 | 设置 → 隐私 → 定位服务 | 高精度 |
| 钉钉位置权限 | 应用管理 → 钉钉 → 权限 | 位置 = **始终允许** |
| 钉钉耗电管理 | 设置 → 电池 → 应用启动管理 | 钉钉改"手动管理"，三个开关全开 |

脚本每轮会执行 `svc power stayon true`（插电常亮）并自动上滑解锁。
实测这台机器的 `screen_off_timeout` 已被调成 600000 毫秒（10 分钟），嫌久可以改回：

```powershell
& $adb -s MKB4C20616034349 shell settings put system screen_off_timeout 30000
```

## 二、电脑侧

adb 用这台机器上 MuMu 自带的 `D:\Program Files\Essentials\NetEase\MuMuPlayer-12.0\shell\adb.exe`
（34.0.1），脚本会自动找；也可设环境变量 `ADB=` 指定别的。

```powershell
$adb = "D:\Program Files\Essentials\NetEase\MuMuPlayer-12.0\shell\adb.exe"
& $adb devices            # 应有 MKB4C20616034349   device
```

脚本只认 **USB 手机**（序列号不含冒号），会自动跳过 MuMu 模拟器 `127.0.0.1:16384`。

## 三、试跑

```powershell
# 1) 走到签到页就停，不点按钮（安全，不会发卡片）
python .\dingtalk_checkin.py --dry-run --force

# 2) 真签一次
python .\dingtalk_checkin.py --force

# 3) 跑完不想让手机息屏
python .\dingtalk_checkin.py --no-sleep
```

运行前后各看一次手机屏幕：开始前应该是息屏（或锁屏），跑完会自己再黑掉。

## 四、定时（一条命令注册）

```powershell
powershell -ExecutionPolicy Bypass -File .\register_checkin_task.ps1
```

默认注册任务「钉钉群签到」，含**两个触发器**：

| 触发器 | 说明 |
|---|---|
| 每天 **06:35** | 到点触发；签到窗 06:30 开始 |
| **登录时**（延迟 2 分钟） | 电脑开机/登录就补一次，适合早上才开机的情况 |

参数：

```powershell
.\register_checkin_task.ps1 -At 06:35 -WindowStart 06:30 -WindowEnd 21:30 -MaxMinutes 15
.\register_checkin_task.ps1 -NoLogonTrigger          # 只要定时触发
.\register_checkin_task.ps1 -Windowless              # 用 pythonw，不弹控制台
Unregister-ScheduledTask -TaskName "钉钉群签到" -Confirm:$false
```

## 五、不会重复签到

两道闸：

1. **本地日期文件** `state\last_checkin.txt`：签到成功后写入当天日期，同一天不再执行。
2. **群里签到卡片**：进群后先找有没有今天的「签到时间：X月X日」卡片，有就跳过。

> 钉钉的消息列表有时不把卡片文字暴露给自动化，所以第 2 道不保证 100%；
> 第 1 道是可靠的。想强制再签：加 `--force`。

## 六、常见问题

| 现象 | 处理 |
|---|---|
| `adb devices` 空 | 开「仅充电模式下允许 ADB 调试」；换根能传数据的线 |
| `unauthorized` | 手机上重新点"允许 USB 调试"并勾"一律允许" |
| 报"仍是锁屏状态" | 关掉锁屏密码，只留上滑解锁 |
| 提示"进不去群聊" | 消息太多把群挤到下方，脚本会先翻一屏；仍不行就把该群置顶 |
| 签到页没自动关闭 | 大概率没签上（定位/网络），看日志和手机屏幕 |
| 跑完手机没息屏 | 用了 `--no-sleep`；或手动 `adb shell input keyevent 223` |
| 换钉钉版本/分辨率后按钮点偏 | 改脚本里的 `SIGN_BTN_FRACTION = (0.5, 0.781)` |
