package cn.iocoder.yudao.module.system.aether;
import org.junit.jupiter.api.*;import org.mockito.*;import org.springframework.test.util.ReflectionTestUtils;
import cn.iocoder.yudao.module.system.dal.mysql.aether.AetherNotificationMapper;
import cn.iocoder.yudao.module.system.service.notify.NotifySendService;
import java.util.Map;import static org.mockito.Mockito.*;import static org.junit.jupiter.api.Assertions.*;
class AetherNotificationServiceTest {
 @Mock AetherNotificationMapper deliveries; @Mock NotifySendService notify; @InjectMocks AetherNotificationService service; AutoCloseable mocks;
 @BeforeEach void init(){mocks=MockitoAnnotations.openMocks(this);ReflectionTestUtils.setField(service,"secret","private-test-key");ReflectionTestUtils.setField(service,"recipients","50001");}
 @AfterEach void close() throws Exception{mocks.close();}
 @Test void missingKeyNeverSends(){assertThrows(RuntimeException.class,()->service.send(null,Map.of()));verifyNoInteractions(notify,deliveries);}
 @Test void replayReturnsStoredReceiptWithoutResending(){Map<String,Object> event=Map.of("alert_id","alert-1","state","firing","metric","request_failures_15m","value",3,"threshold",2);
  when(deliveries.reserve(anyString(),anyString(),anyString())).thenReturn(0);when(deliveries.receipt("alert-1","firing")).thenReturn("[123]");
  assertEquals("[123]",service.send("private-test-key",event).get("message_ids"));verifyNoInteractions(notify);
 }
}
