package cn.iocoder.yudao.framework.web.core.handler;

import ch.qos.logback.classic.Logger;
import ch.qos.logback.classic.spi.ILoggingEvent;
import ch.qos.logback.core.read.ListAppender;
import cn.iocoder.yudao.framework.common.biz.infra.logger.ApiErrorLogCommonApi;
import cn.iocoder.yudao.framework.common.biz.infra.logger.dto.ApiErrorLogCreateReqDTO;
import org.junit.jupiter.api.*;
import org.mockito.ArgumentCaptor;
import org.slf4j.LoggerFactory;
import org.springframework.core.MethodParameter;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.http.MockHttpInputMessage;
import org.springframework.validation.BeanPropertyBindingResult;
import org.springframework.validation.BindException;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class AetherExceptionPrivacyTest {
    private static final String SENTINEL = "test-password-must-never-be-logged";
    private final ApiErrorLogCommonApi api = mock(ApiErrorLogCommonApi.class);
    private final GlobalExceptionHandler handler = new GlobalExceptionHandler("test", api);
    private final Logger logger = (Logger) LoggerFactory.getLogger(GlobalExceptionHandler.class);
    private ListAppender<ILoggingEvent> appender;
    @BeforeEach void start() { appender = new ListAppender<>(); appender.start(); logger.addAppender(appender); }
    @AfterEach void stop() { logger.detachAppender(appender); appender.stop(); }
    public void login(Object body) {}
    private BeanPropertyBindingResult rejectedPassword() {
        var result = new BeanPropertyBindingResult(Map.of("password", SENTINEL), "login");
        result.addError(new FieldError("login", "password", SENTINEL, false, new String[]{"Size"}, null, "length invalid"));
        return result;
    }
    private void assertSafeLogs() {
        assertFalse(appender.list.isEmpty());
        for (var event : appender.list) {
            assertFalse(event.getFormattedMessage().contains(SENTINEL));
            assertNull(event.getThrowableProxy(), "Raw exception can contain rejected credentials");
        }
    }
    @Test void invalidPasswordValidationDoesNotLogRejectedValue() throws Exception {
        var method = getClass().getMethod("login", Object.class);
        assertEquals(400, handler.methodArgumentNotValidExceptionExceptionHandler(new MethodArgumentNotValidException(new MethodParameter(method, 0), rejectedPassword())).getCode());
        assertSafeLogs();
    }
    @Test void bindingFailureDoesNotLogRejectedValue() {
        assertEquals(400, handler.bindExceptionHandler(new BindException(rejectedPassword())).getCode());
        assertSafeLogs();
    }
    @Test void malformedJsonDoesNotFallThroughToRawErrorLogging() {
        var error = new HttpMessageNotReadableException(SENTINEL, new MockHttpInputMessage(new byte[0]));
        var response = handler.methodArgumentTypeInvalidFormatExceptionHandler(error);
        assertEquals(400, response.getCode());
        assertFalse(response.getMsg().contains(SENTINEL));
        assertSafeLogs();
    }
    @Test void sensitiveEndpointErrorDoesNotPersistPayloadOrExceptionValues() throws Exception {
        for (String path : new String[]{"/admin-api/aether/identity/login", "/admin-api/aether/ops/commands", "/admin-api/system/auth/login"}) {
            var request = new MockHttpServletRequest("POST", path);
            request.setContent(("{\"password\":\"" + SENTINEL + "\"}").getBytes());
            request.addHeader("Authorization", SENTINEL);
            request.addParameter("access_token", SENTINEL);
            assertEquals(500, handler.defaultExceptionHandler(request, new IllegalStateException(SENTINEL)).getCode());
        }
        var captured = ArgumentCaptor.forClass(ApiErrorLogCreateReqDTO.class);
        verify(api, times(3)).createApiErrorLogAsync(captured.capture());
        for (var dto : captured.getAllValues()) {
            for (var field : ApiErrorLogCreateReqDTO.class.getDeclaredFields()) {
                if (field.isAnnotationPresent(jakarta.validation.constraints.NotNull.class)) {
                    field.setAccessible(true);
                    assertNotNull(field.get(dto), "Required error-log field: " + field.getName());
                }
            }
            assertFalse(String.valueOf(dto.getRequestParams()).contains(SENTINEL));
            assertFalse(String.valueOf(dto.getExceptionMessage()).contains(SENTINEL));
            assertFalse(String.valueOf(dto.getExceptionRootCauseMessage()).contains(SENTINEL));
            assertFalse(String.valueOf(dto.getExceptionStackTrace()).contains(SENTINEL));
        }
        assertSafeLogs();
    }
}
