"""Structured log format strings for exporter-managed BIG-IP logging profiles."""

REQUEST_EVENT_TEMPLATE = (
    'event_source="request_logging",hostname="$BIGIP_HOSTNAME",client_ip="$CLIENT_IP",'
    'server_ip="$SERVER_IP",http_method="$HTTP_METHOD",http_uri="$HTTP_URI",'
    'virtual_name="$VIRTUAL_NAME",event_timestamp="$DATE_HTTP"'
)
RESPONSE_EVENT_TEMPLATE = (
    'event_source="response_logging",hostname="$BIGIP_HOSTNAME",client_ip="$CLIENT_IP",'
    'server_ip="$SERVER_IP",http_method="$HTTP_METHOD",http_uri="$HTTP_URI",'
    'virtual_name="$VIRTUAL_NAME",event_timestamp="$DATE_HTTP",'
    'http_statcode="$HTTP_STATCODE",http_status="$HTTP_STATUS",response_ms="$RESPONSE_MSECS"'
)
AFM_NETWORK_EVENT_TEMPLATE = (
    'event_source="afm_network_logging",hostname="$BIGIP_HOSTNAME",'
    'src_ip="$SRCIP",dest_ip="$DESTIP",src_port="$SRCPORT",dest_port="$DESTPORT",'
    'vlan="$VLAN",action="$ACTION",rule_name="$RULE_NAME",event_timestamp="$DATE_HTTP"'
)
