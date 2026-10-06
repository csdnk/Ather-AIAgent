package cn.iocoder.yudao.module.system.dal.mysql.aether;
import org.apache.ibatis.annotations.*;
import com.baomidou.mybatisplus.annotation.InterceptorIgnore;
@Mapper @InterceptorIgnore(tenantLine="true")
public interface AetherNotificationMapper {
 @Insert("INSERT IGNORE INTO aether_notification_delivery(alert_id,state,payload_hash) VALUES(#{alertId},#{state},#{hash})")
 int reserve(@Param("alertId") String alertId,@Param("state") String state,@Param("hash") String hash);
 @Select("SELECT message_ids FROM aether_notification_delivery WHERE alert_id=#{alertId} AND state=#{state}")
 String receipt(@Param("alertId") String alertId,@Param("state") String state);
 @Update("UPDATE aether_notification_delivery SET message_ids=#{receipt},completed_at=CURRENT_TIMESTAMP WHERE alert_id=#{alertId} AND state=#{state}")
 int complete(@Param("alertId") String alertId,@Param("state") String state,@Param("receipt") String receipt);
}
