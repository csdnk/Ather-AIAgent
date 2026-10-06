package cn.iocoder.yudao.module.system.aether;

import cn.iocoder.yudao.framework.common.exception.ServiceException;
import cn.iocoder.yudao.framework.datapermission.core.annotation.DataPermission;
import cn.iocoder.yudao.framework.security.core.util.SecurityFrameworkUtils;
import cn.iocoder.yudao.framework.tenant.core.util.TenantUtils;
import cn.iocoder.yudao.module.system.dal.dataobject.permission.*;
import cn.iocoder.yudao.module.system.dal.mysql.permission.*;
import cn.iocoder.yudao.module.system.dal.mysql.user.AdminUserMapper;
import cn.iocoder.yudao.module.system.dal.mysql.tenant.TenantMapper;
import cn.iocoder.yudao.module.system.dal.mysql.oauth2.*;
import cn.iocoder.yudao.module.system.dal.dataobject.oauth2.OAuth2AccessTokenDO;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import jakarta.annotation.Resource;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import java.time.LocalDateTime;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.*;

/** Live directory authority: bypass role caches so disabled/revoked roles close access immediately. */
@Service("aetherIdentity")
@DataPermission(enable=false)
public class AetherIdentityService {
 @Resource AdminUserMapper users; @Resource TenantMapper tenants; @Resource UserRoleMapper userRoles;
 @Resource RoleMapper roles; @Resource RoleMenuMapper roleMenus; @Resource MenuMapper menus;
 @Resource OAuth2ClientMapper clients; @Resource OAuth2AccessTokenMapper tokens;
 @Value("${aether.identity.platform-tenant-id:1}") private Long platformTenantId;
 @Value("${aether.identity.service-clients:aether-python}") private String serviceClients;
 public record Identity(String user_id,String tenant_id,String username,String display_name,boolean user_enabled,boolean tenant_enabled,List<String> role_codes,List<String> permissions){}
 public Identity self(){
  var login=SecurityFrameworkUtils.getLoginUser();
  if(login==null||!Integer.valueOf(2).equals(login.getUserType())||login.getTenantId()==null)throw new ServiceException(401,"Authenticated administrative identity required");
  return load(login.getId(),login.getTenantId());
 }
 public Identity load(Long userId,Long tenantId){return TenantUtils.execute(tenantId,()->{
  var user=users.selectById(userId);
  if(user==null||!Objects.equals(user.getTenantId(),tenantId)||!Integer.valueOf(0).equals(user.getStatus()))throw new ServiceException(403,"User unavailable");
  var tenant=tenants.selectById(tenantId);
  if(tenant==null||!Integer.valueOf(0).equals(tenant.getStatus())||tenant.getExpireTime()==null||!tenant.getExpireTime().isAfter(LocalDateTime.now()))throw new ServiceException(403,"Tenant unavailable");
  var ids=userRoles.selectListByUserId(userId).stream().map(UserRoleDO::getRoleId).toList();
  var liveRoles=ids.isEmpty()?List.<RoleDO>of():roles.selectByIds(ids).stream().filter(r->Integer.valueOf(0).equals(r.getStatus())).toList();
  var codes=liveRoles.stream().map(RoleDO::getCode).map(AetherIdentityService::aetherRoleCode).filter(c->Set.of("aether_platform_admin","aether_tenant_admin","aether_user").contains(c))
   .filter(c->!c.equals("aether_platform_admin")||Objects.equals(tenantId,platformTenantId)).distinct().sorted().toList();
  List<String> permissions=List.of();
  if(codes.contains("aether_platform_admin")||codes.contains("aether_tenant_admin")){
   var menuIds=liveRoles.isEmpty()?List.<Long>of():roleMenus.selectListByRoleId(liveRoles.stream().map(RoleDO::getId).toList()).stream().map(RoleMenuDO::getMenuId).distinct().toList();
   if(!menuIds.isEmpty())permissions=menus.selectByIds(menuIds).stream().filter(m->Integer.valueOf(0).equals(m.getStatus())).map(MenuDO::getPermission).filter(p->p!=null&&!p.isBlank()).distinct().sorted().toList();
  }
  return new Identity(userId.toString(),tenantId.toString(),user.getUsername(),Objects.toString(user.getNickname(),user.getUsername()),true,true,codes,permissions);
 });}
 public boolean hasPermission(String permission){return self().permissions().contains(permission);}
 public void validateServiceClient(String[] basic){
  if(basic==null||basic.length!=2||!Arrays.asList(serviceClients.split(",")).contains(basic[0]))throw new ServiceException(401,"Service client required");
  var client=clients.selectByClientId(basic[0]);
  if(client==null||!Integer.valueOf(0).equals(client.getStatus())||!secretMatches(client.getSecret(),basic[1]))throw new ServiceException(401,"Invalid service client");
 }
 public void validateTokenHash(Long userId,Long tenantId,String hash){
  if(hash==null||!hash.matches("[a-f0-9]{64}"))throw new ServiceException(401,"Token fingerprint required");
  boolean active=TenantUtils.execute(tenantId,()->tokens.selectList(new LambdaQueryWrapper<OAuth2AccessTokenDO>().eq(OAuth2AccessTokenDO::getUserId,userId).eq(OAuth2AccessTokenDO::getUserType,2).gt(OAuth2AccessTokenDO::getExpiresTime,LocalDateTime.now())).stream().anyMatch(t->sha256(t.getAccessToken()).equals(hash)));
  if(!active)throw new ServiceException(401,"Token expired or revoked");
 }
 public static String aetherRoleCode(String code){return "tenant_admin".equals(code)?"aether_tenant_admin":code;}
 public static boolean secretMatches(String expected,String supplied){return expected!=null&&!expected.isBlank()&&supplied!=null&&MessageDigest.isEqual(expected.getBytes(StandardCharsets.UTF_8),supplied.getBytes(StandardCharsets.UTF_8));}
 public static String sha256(String value){try{return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(value.getBytes(StandardCharsets.UTF_8)));}catch(Exception e){throw new IllegalStateException(e);}}
}
