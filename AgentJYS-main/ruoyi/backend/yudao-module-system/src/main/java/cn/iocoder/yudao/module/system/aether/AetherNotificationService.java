package cn.iocoder.yudao.module.system.aether;
import cn.iocoder.yudao.framework.common.exception.ServiceException;
import cn.iocoder.yudao.framework.tenant.core.util.TenantUtils;
import cn.iocoder.yudao.module.system.dal.mysql.aether.AetherNotificationMapper;
import cn.iocoder.yudao.module.system.service.notify.NotifySendService;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.*;
@Service
public class AetherNotificationService {
 @Resource AetherNotificationMapper deliveries; @Resource NotifySendService notify;
 @Value("${aether.notification-key:}") String secret;
 @Value("${aether.notification-admin-ids:}") String recipients;
 @Value("${aether.identity.platform-tenant-id:1}") Long platformTenantId;
 @Transactional(rollbackFor=Exception.class)
 public Map<String,Object> send(String key,Map<String,Object> event){
  if(!AetherIdentityService.secretMatches(secret,key))throw new ServiceException(401,"Notification service key required");
  if(!Set.of("alert_id","metric","tenant_id","state","value","threshold").containsAll(event.keySet()))throw new ServiceException(400,"Unsupported alert field");
  String id=Objects.toString(event.get("alert_id"),""),state=Objects.toString(event.get("state"),"");
  if(!id.matches("[A-Za-z0-9_-]{1,128}")||!Set.of("firing","open","resolved","silenced","active").contains(state)||Objects.toString(event.get("metric"),"").length()>100)throw new ServiceException(400,"Invalid alert identity");
  if(recipients.isBlank())throw new ServiceException(503,"Notification recipients not configured");
  var params=new TreeMap<String,Object>();for(String field:List.of("alert_id","metric","tenant_id","state","value","threshold"))params.put(field,Objects.toString(event.get(field),"unknown"));
  if(deliveries.reserve(id,state,AetherIdentityService.sha256(params.toString()))==0){String prior=deliveries.receipt(id,state);if(prior==null)throw new ServiceException(503,"Notification receipt pending");return Map.of("status","delivered","message_ids",prior,"replayed",true);}
  List<Long> receipts=TenantUtils.execute(platformTenantId,()->{
   List<Long> result=new ArrayList<>();for(String user:recipients.split(",")){Long receipt=notify.sendSingleNotifyToAdmin(Long.valueOf(user.trim()),"aether_ops_alert",params);if(receipt==null)throw new ServiceException(503,"Notification template disabled");result.add(receipt);}return result;});
  deliveries.complete(id,state,receipts.toString());return Map.of("status","delivered","message_ids",receipts.toString(),"replayed",false);
 }
}
