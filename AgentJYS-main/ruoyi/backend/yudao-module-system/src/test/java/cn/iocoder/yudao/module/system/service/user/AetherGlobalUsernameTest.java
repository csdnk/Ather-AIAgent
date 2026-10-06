package cn.iocoder.yudao.module.system.service.user;
import org.junit.jupiter.api.Test;import org.springframework.test.util.ReflectionTestUtils;
import cn.iocoder.yudao.module.system.dal.mysql.user.AdminUserMapper;
import cn.iocoder.yudao.framework.tenant.core.context.TenantContextHolder;
import static org.mockito.Mockito.*;import static org.junit.jupiter.api.Assertions.*;
class AetherGlobalUsernameTest {
 @Test void uniquenessSearchMustIncludeOtherTenants(){var mapper=mock(AdminUserMapper.class);var service=new AdminUserServiceImpl();ReflectionTestUtils.setField(service,"userMapper",mapper);
  when(mapper.selectByUsername("globaluser")).thenAnswer(invocation->{assertTrue(TenantContextHolder.isIgnore(),"uniqueness must be global");return null;});
  service.validateUsernameUnique(null,"globaluser");verify(mapper).selectByUsername("globaluser");assertFalse(TenantContextHolder.isIgnore());
 }
}
