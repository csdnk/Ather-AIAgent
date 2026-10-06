package cn.iocoder.yudao.module.system.controller.admin.aether;
import cn.iocoder.yudao.framework.common.pojo.CommonResult;
import cn.iocoder.yudao.framework.apilog.core.annotation.ApiAccessLog;
import cn.iocoder.yudao.framework.tenant.core.aop.TenantIgnore;
import cn.iocoder.yudao.module.system.aether.AetherNotificationService;
import jakarta.annotation.Resource;
import jakarta.annotation.security.PermitAll;
import org.springframework.web.bind.annotation.*;
import java.util.Map;
@RestController @RequestMapping("/aether/notifications")
public class AetherNotificationController {
 @Resource AetherNotificationService service;
 @PostMapping("/alert") @PermitAll @TenantIgnore @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<Map<String,Object>> alert(@RequestHeader(value="X-Aether-Notification-Key",required=false) String key,@RequestBody Map<String,Object> event){return CommonResult.success(service.send(key,event));}
}
