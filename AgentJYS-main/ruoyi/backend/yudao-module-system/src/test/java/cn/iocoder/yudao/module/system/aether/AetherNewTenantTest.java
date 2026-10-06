package cn.iocoder.yudao.module.system.aether;
import org.junit.jupiter.api.Test;import org.mockito.ArgumentCaptor;import org.springframework.test.util.ReflectionTestUtils;
import cn.iocoder.yudao.module.system.service.tenant.TenantServiceImpl;
import cn.iocoder.yudao.module.system.service.permission.*;
import cn.iocoder.yudao.module.system.controller.admin.permission.vo.role.RoleSaveReqVO;
import cn.iocoder.yudao.module.system.dal.dataobject.tenant.TenantPackageDO;
import java.util.Set;import static org.junit.jupiter.api.Assertions.*;import static org.mockito.Mockito.*;
class AetherNewTenantTest {
 @Test void newTenantKeepsNativeAdministratorAndGetsOrdinaryAetherRole(){
  var service=new TenantServiceImpl();var roles=mock(RoleService.class);var permissions=mock(PermissionService.class);
  ReflectionTestUtils.setField(service,"roleService",roles);ReflectionTestUtils.setField(service,"permissionService",permissions);
  when(roles.createRole(any(),any())).thenReturn(55L,56L);var pack=new TenantPackageDO();pack.setMenuIds(Set.of(60000L));
  ReflectionTestUtils.invokeMethod(service,"createRole",pack);var requests=ArgumentCaptor.forClass(RoleSaveReqVO.class);verify(roles,times(2)).createRole(requests.capture(),any());
  assertEquals(Set.of("tenant_admin","aether_user"),requests.getAllValues().stream().map(RoleSaveReqVO::getCode).collect(java.util.stream.Collectors.toSet()));verify(permissions).assignRoleMenu(55L,Set.of(60000L));verifyNoMoreInteractions(permissions);
 }
 @Test void nativeTenantAdministratorMapsToTenantOnly(){assertEquals("aether_tenant_admin",AetherIdentityService.aetherRoleCode("tenant_admin"));assertEquals("aether_user",AetherIdentityService.aetherRoleCode("aether_user"));}
}
