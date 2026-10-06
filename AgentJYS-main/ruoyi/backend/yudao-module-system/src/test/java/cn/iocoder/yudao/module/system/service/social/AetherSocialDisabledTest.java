package cn.iocoder.yudao.module.system.service.social;

import cn.iocoder.yudao.framework.common.exception.ServiceException;
import cn.iocoder.yudao.module.system.dal.mysql.social.SocialClientMapper;
import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.runner.ApplicationContextRunner;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.test.util.ReflectionTestUtils;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AetherSocialDisabledTest {
    @Test void serviceStartsWithoutWechatCredentialsAndRejectsUnavailableCalls() {
        new ApplicationContextRunner()
                .withPropertyValues("aether.social.enabled=false")
                .withBean(SocialClientMapper.class, () -> mock(SocialClientMapper.class))
                .withBean(StringRedisTemplate.class, () -> mock(StringRedisTemplate.class))
                .withBean(SocialClientServiceImpl.class)
                .run(context -> {
                    assertNull(context.getStartupFailure());
                    SocialClientServiceImpl service = context.getBean(SocialClientServiceImpl.class);
                    assertUnavailable(() -> service.buildAuthRequest(31, 2));
                    assertUnavailable(() -> service.getWxMpService(2));
                    assertUnavailable(() -> service.getWxMaService(2));
                    assertUnavailable(() -> service.buildWxMpService("", ""));
                    verifyNoInteractions(context.getBean(SocialClientMapper.class));
                });
    }

    @Test void missingDefaultServicesFailExplicitlyWhenSocialIsEnabled() {
        SocialClientServiceImpl service = new SocialClientServiceImpl();
        SocialClientMapper mapper = mock(SocialClientMapper.class);
        ReflectionTestUtils.setField(service, "socialClientMapper", mapper);
        assertUnavailable(() -> service.buildAuthRequest(31, 2));
        assertUnavailable(() -> service.getWxMpService(2));
        assertUnavailable(() -> service.getWxMaService(2));
    }

    private static void assertUnavailable(org.junit.jupiter.api.function.Executable action) {
        ServiceException error = assertThrows(ServiceException.class, action);
        assertEquals(501, error.getCode());
    }
}
