# -*- coding: utf-8 -*-
"""
串口协议编解码 —— PC-C 上位机 <-> STM32 风机控制器（USART2）

帧格式（ASCII，\\r\\n 结尾，字段以 , 分隔，无校验）：
  MCU -> PC 遥测 :  $WIND,<cycle>,<wind_speed>,<power_available>,<power_set>,<power_actual>,<status>,<deg>,<control_mode>
  PC  -> MCU 参数:  $PARAM,<cut_in_speed_mps>,<rated_speed_mps>,<cut_out_speed_mps>,<wind_rated_power_kw>,<pitch_feather_deg>,<c_control_s>,<c_timeout_s>,<control_mode>
  PC  -> MCU 命令:  $CMD,START|STOP|RESET|AUTO|MANUAL
  MCU -> PC  应答:  $ACK,<PARAM|CMD>,<0|1>

字段顺序以 config.PARAM_FIELDS / config.WIND_FIELDS 为准。

线格式 8 字段顺序不变（与固件一致），Python 侧按 WIND_FIELDS 的位置映射为对齐 A/B 仓库的字段名：
  wind_speed_mps / wind_available_kw / wind_target_kw / wind_actual_kw / wind_running / pitch_target_deg。
wind_operating_limit_kw 不在线格式中，由本地 mock（simulator.py）额外补齐，联调后由 A 的 state 提供。
"""

from config import PARAM_FIELDS, WIND_FIELDS


def _fmt(v):
    """把数值格式化为字符串：int 转 int，float 保留 2 位。"""
    if isinstance(v, int):
        return str(v)
    return f"{float(v):.2f}"


def build_param_frame(params):
    """根据参数字典生成 $PARAM 帧（bytes）。"""
    parts = [_fmt(params[k]) for k in PARAM_FIELDS]
    return ("$PARAM," + ",".join(parts) + "\r\n").encode("ascii")


def build_cmd_frame(command):
    """根据命令名（START/STOP/RESET/AUTO/MANUAL）生成 $CMD 帧（bytes）。"""
    return f"$CMD,{command}\r\n".encode("ascii")


def build_wifi_frame(ip, port):
    """生成 $WIFI,<ip>,<port> 设置帧（bytes），让 MCU 更新 A 地址/端口并重连。"""
    return f"$WIFI,{ip},{int(port)}\r\n".encode("ascii")


def build_wifi_query_frame():
    """生成 $WIFI? 查询帧（bytes），查询 MCU 当前 A 地址/端口。"""
    return b"$WIFI?\r\n"


def parse_frame(line):
    """解析一帧，返回 (类型, 内容) 或 None。

    返回:
      ("wind", dict)   遥测帧，dict 键为 WIND_FIELDS
      ("ack",  (type, ok))
      ("other", raw)
      None             无法识别
    """
    if not isinstance(line, str):
        try:
            line = line.decode("ascii", errors="ignore")
        except Exception:
            return None
    line = line.strip()
    if not line.startswith("$"):
        return None

    body = line[1:]
    kind, sep, rest = body.partition(",")
    if not sep:
        return None

    if kind == "WIND":
        fields = rest.split(",")
        if len(fields) != len(WIND_FIELDS):
            return None
        data = {}
        for name, raw in zip(WIND_FIELDS, fields):
            try:
                if name in ("cycle", "control_mode", "link_status"):
                    data[name] = int(float(raw))
                elif name == "wind_running":
                    data[name] = bool(int(float(raw)))
                else:
                    data[name] = float(raw)
            except ValueError:
                return None
        return ("wind", data)

    if kind == "ACK":
        fields = rest.split(",")
        if len(fields) >= 2:
            try:
                return ("ack", (fields[0], int(fields[1])))
            except ValueError:
                return ("ack", (fields[0], 0))
        return None

    if kind == "CMD":
        return ("cmd", rest)
    if kind == "PARAM":
        return ("param", rest)

    if kind == "WIFIGET":
        fields = rest.split(",")
        if len(fields) >= 2:
            return ("wifiget", (fields[0], fields[1]))
        return None

    return ("other", body)


def parse_wind(line):
    """便捷方法：仅解析遥测帧，返回 dict 或 None。"""
    r = parse_frame(line)
    if r and r[0] == "wind":
        return r[1]
    return None
