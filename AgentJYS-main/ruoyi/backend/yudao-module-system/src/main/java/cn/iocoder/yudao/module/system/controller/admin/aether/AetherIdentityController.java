package cn.iocoder.yudao.module.system.controller.admin.aether;
import cn.iocoder.yudao.framework.common.pojo.CommonResult;
import cn.iocoder.yudao.framework.common.exception.ServiceException;
import cn.iocoder.yudao.framework.common.util.http.HttpUtils;
import cn.iocoder.yudao.framework.apilog.core.annotation.ApiAccessLog;
import cn.iocoder.yudao.framework.tenant.core.aop.TenantIgnore;
import cn.iocoder.yudao.framework.tenant.core.util.TenantUtils;
import cn.iocoder.yudao.module.system.aether.AetherIdentityService;
import cn.iocoder.yudao.module.system.dal.mysql.user.AdminUserMapper;
import cn.iocoder.yudao.module.system.dal.mysql.tenant.TenantMapper;
import cn.iocoder.yudao.module.system.dal.dataobject.user.AdminUserDO;
import cn.iocoder.yudao.module.system.service.auth.AdminAuthService;
import cn.iocoder.yudao.module.system.controller.admin.auth.vo.*;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import jakarta.annotation.Resource;
import jakarta.annotation.security.PermitAll;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.*;
import java.time.LocalDateTime;
import static cn.iocoder.yudao.framework.common.pojo.CommonResult.success;
@RestController
@RequestMapping("/aether/identity")
public class AetherIdentityController {
 @Resource AetherIdentityService identity; @Resource AdminUserMapper users; @Resource TenantMapper tenants; @Resource AdminAuthService auth;
 @GetMapping("/self") @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<AetherIdentityService.Identity> self(){return success(identity.self());}
 @PostMapping("/status") @PermitAll @TenantIgnore @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<AetherIdentityService.Identity> status(HttpServletRequest request,@RequestParam("user_id") Long userId,@RequestParam("tenant_id") Long tenantId,@RequestParam(value="token_hash",required=false) String hash){
  identity.validateServiceClient(HttpUtils.obtainBasicAuthorization(request));
  if(hash!=null)identity.validateTokenHash(userId,tenantId,hash);
  return success(identity.load(userId,tenantId));
 }
 @PostMapping("/login") @PermitAll @TenantIgnore @ApiAccessLog(requestEnable=false,responseEnable=false)
 public CommonResult<AuthLoginRespVO> login(@RequestBody @Valid AuthLoginReqVO input){
  // Username is a login identifier only; ambiguity is denied rather than selecting a tenant.
  var matches=TenantUtils.executeIgnore(()->users.selectList(new LambdaQueryWrapper<AdminUserDO>().eq(AdminUserDO::getUsername,input.getUsername()).last("LIMIT 2")));
  if(matches.size()!=1)throw new ServiceException(401,"Invalid account or credentials");
  var tenant=tenants.selectById(matches.get(0).getTenantId());
  if(tenant==null||!Integer.valueOf(0).equals(tenant.getStatus())||tenant.getExpireTime()==null||!tenant.getExpireTime().isAfter(LocalDateTime.now()))throw new ServiceException(401,"Invalid account or credentials");
  return success(TenantUtils.execute(tenant.getId(),()->auth.login(input)));
 }
}
