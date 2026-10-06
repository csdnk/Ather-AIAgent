package cn.iocoder.yudao.module.system.controller.admin.aether;
import cn.iocoder.yudao.framework.common.pojo.CommonResult;
import cn.iocoder.yudao.framework.common.exception.ServiceException;
import cn.iocoder.yudao.framework.apilog.core.annotation.ApiAccessLog;
import cn.iocoder.yudao.module.system.aether.*;
import com.fasterxml.jackson.databind.JsonNode;
import jakarta.annotation.Resource;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import java.util.Map;
import static cn.iocoder.yudao.framework.common.pojo.CommonResult.success;
@RestController @RequestMapping("/aether/ops")
public class AetherOpsController {
 @Resource AetherOpsGateway gateway; @Resource AetherIdentityService identity;
 @GetMapping("/{resource}") @PreAuthorize("@aetherIdentity.hasPermission('aether:ops:read')")
 @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<JsonNode> read(@PathVariable String resource,@RequestHeader("Authorization") String bearer,@RequestParam Map<String,String> query){return success(gateway.read(resource,bearer,query));}
 @GetMapping({"/console/{resource}","/console/{resource}/{id}"}) @PreAuthorize("@aetherIdentity.hasPermission('aether:ops:read')")
 @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<JsonNode> console(@PathVariable String resource,@PathVariable(required=false) String id,@RequestHeader("Authorization") String bearer,@RequestParam Map<String,String> query,jakarta.servlet.http.HttpServletResponse response){response.setHeader("Cache-Control","no-store");return success(gateway.console(resource,id,bearer,query));}
 @PostMapping("/commands") @PreAuthorize("@aetherIdentity.hasPermission('aether:ops:read')")
 @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<JsonNode> command(@RequestHeader("Authorization") String bearer,@RequestBody JsonNode body){
  String resource=body.path("resource").asText();
  if(!AetherOpsGateway.RESOURCES.contains(resource)||!identity.hasPermission("aether:"+resource+":execute"))throw new ServiceException(403,"Operation permission required");
  return success(gateway.command(bearer,body));
 }
}
