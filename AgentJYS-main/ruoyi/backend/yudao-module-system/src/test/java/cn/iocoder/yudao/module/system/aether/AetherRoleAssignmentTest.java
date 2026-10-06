package cn.iocoder.yudao.module.system.aether;
import org.junit.jupiter.api.Test;import org.springframework.test.util.ReflectionTestUtils;
import cn.iocoder.yudao.module.system.service.permission.*;import cn.iocoder.yudao.module.system.service.user.AdminUserService;
import cn.iocoder.yudao.module.system.dal.mysql.permission.UserRoleMapper;
import java.util.Set;import java.util.List;import static org.mockito.Mockito.*;import static org.junit.jupiter.api.Assertions.*;
class AetherRoleAssignmentTest {
 @Test void rejectsRoleIdsAbsentFromCurrentTenant(){var service=new PermissionServiceImpl();var users=mock(AdminUserService.class);var roles=mock(RoleService.class);var mapping=mock(UserRoleMapper.class);
  ReflectionTestUtils.setField(service,"userService",users);ReflectionTestUtils.setField(service,"roleService",roles);ReflectionTestUtils.setField(service,"userRoleMapper",mapping);when(roles.getRoleList(Set.of(999L))).thenReturn(List.of());
  assertThrows(RuntimeException.class,()->service.assignUserRole(5L,Set.of(999L)));verify(users).validateUserList(Set.of(5L));verifyNoInteractions(mapping);
 }
}
