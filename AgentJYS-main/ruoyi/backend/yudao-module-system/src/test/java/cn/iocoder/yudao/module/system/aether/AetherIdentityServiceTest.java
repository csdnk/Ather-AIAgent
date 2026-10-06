package cn.iocoder.yudao.module.system.aether;
import org.junit.jupiter.api.*;
import org.mockito.*;
import org.springframework.test.util.ReflectionTestUtils;
import cn.iocoder.yudao.module.system.dal.mysql.user.AdminUserMapper;
import cn.iocoder.yudao.module.system.dal.mysql.tenant.TenantMapper;
import cn.iocoder.yudao.module.system.dal.mysql.permission.*;
import cn.iocoder.yudao.module.system.dal.dataobject.user.AdminUserDO;
import cn.iocoder.yudao.module.system.dal.dataobject.tenant.TenantDO;
import cn.iocoder.yudao.module.system.dal.dataobject.permission.*;
import java.time.LocalDateTime;
import java.util.List;
import static org.mockito.Mockito.*;
import static org.junit.jupiter.api.Assertions.*;
class AetherIdentityServiceTest {
 @Mock AdminUserMapper users; @Mock TenantMapper tenants; @Mock UserRoleMapper userRoles;
 @Mock RoleMapper roles; @Mock RoleMenuMapper roleMenus; @Mock MenuMapper menus;
 @InjectMocks AetherIdentityService service;
 AutoCloseable mocks;
 @BeforeEach void init(){mocks=MockitoAnnotations.openMocks(this);ReflectionTestUtils.setField(service,"platformTenantId",1L);}
 @AfterEach void close() throws Exception{mocks.close();}
 @Test void rejectsCrossTenantUser(){AdminUserDO u=new AdminUserDO();u.setId(9L);u.setTenantId(2L);u.setStatus(0);when(users.selectById(9L)).thenReturn(u);assertThrows(RuntimeException.class,()->service.load(9L,1L));}
 @Test void disabledUserCannotKeepPrivileges(){AdminUserDO u=new AdminUserDO();u.setId(9L);u.setTenantId(1L);u.setStatus(1);when(users.selectById(9L)).thenReturn(u);assertThrows(RuntimeException.class,()->service.load(9L,1L));}
 @Test void roleRevocationIsReadOnEveryRequest(){
  AdminUserDO u=new AdminUserDO();u.setId(9L);u.setTenantId(1L);u.setStatus(0);u.setUsername("tester");u.setNickname("Tester");
  TenantDO t=new TenantDO();t.setStatus(0);t.setExpireTime(LocalDateTime.now().plusDays(1));
  when(users.selectById(9L)).thenReturn(u);when(tenants.selectById(1L)).thenReturn(t);when(userRoles.selectListByUserId(9L)).thenReturn(List.of());
  assertTrue(service.load(9L,1L).role_codes().isEmpty());assertTrue(service.load(9L,1L).permissions().isEmpty());verify(userRoles,times(2)).selectListByUserId(9L);
 }
 @Test void blankNotificationSecretNeverAuthenticates(){assertFalse(AetherIdentityService.secretMatches("", ""));assertFalse(AetherIdentityService.secretMatches(null,"x"));assertTrue(AetherIdentityService.secretMatches("valid-secret","valid-secret"));}
}
