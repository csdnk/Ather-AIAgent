package cn.iocoder.yudao.module.system.aether;
import com.sun.net.httpserver.HttpServer;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.*;
import java.net.InetSocketAddress;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import static org.junit.jupiter.api.Assertions.*;
class AetherOpsGatewayTest {
 HttpServer server; AetherOpsGateway gateway; AtomicInteger calls;
 @BeforeEach void setUp() throws Exception {
  calls=new AtomicInteger(); server=HttpServer.create(new InetSocketAddress("127.0.0.1",0),0);
  server.createContext("/", exchange -> { calls.incrementAndGet();
   assertEquals("Bearer opaque-token",exchange.getRequestHeaders().getFirst("Authorization"));
   assertNull(exchange.getRequestHeaders().getFirst("tenant-id"));
   byte[] body="{\"items\":[],\"total\":0,\"status\":\"ok\"}".getBytes();
   exchange.getResponseHeaders().add("Content-Type","application/json");exchange.sendResponseHeaders(200,body.length);
   exchange.getResponseBody().write(body);exchange.close(); });server.start();
  gateway=new AetherOpsGateway("http://127.0.0.1:"+server.getAddress().getPort(),new ObjectMapper());
 }
 @AfterEach void stop(){server.stop(0);}
 @Test void forwardsOnlyOpaqueTokenToFixedResource() {var data=gateway.read("tasks","Bearer opaque-token",Map.of("limit","10"));assertEquals(0,data.get("total").asInt());assertEquals(1,calls.get());}
 @Test void rejectsArbitraryTargetBeforeNetwork(){assertThrows(IllegalArgumentException.class,()->gateway.read("http://attacker/","Bearer opaque-token",Map.of()));assertEquals(0,calls.get());}
 @Test void rejectsTenantAndUrlQueryInjection(){assertThrows(IllegalArgumentException.class,()->gateway.read("tasks","Bearer opaque-token",Map.of("tenant_id","other")));assertEquals(0,calls.get());}
 @Test void rejectsMissingBearer(){assertThrows(IllegalArgumentException.class,()->gateway.read("tasks","",Map.of()));assertEquals(0,calls.get());}
 @Test void rejectsCommandWithoutStableId(){assertThrows(IllegalArgumentException.class,()->gateway.command("Bearer opaque-token",new ObjectMapper().createObjectNode().put("resource","tasks").put("action","cancel")));assertEquals(0,calls.get());}
 @Test void rejectsOriginWithPathOrCredentials(){assertThrows(IllegalArgumentException.class,()->new AetherOpsGateway("http://user:pass@localhost/path",new ObjectMapper()));}
 @Test void forwardsConsoleUserSelectorAsQueryNotIdentity(){var data=gateway.console("memories","memory_1","Bearer opaque-token",Map.of("user_id","demo_user_a"));assertEquals("ok",data.path("status").asText());assertEquals(1,calls.get());}
 @Test void rejectsConsolePathTraversalBeforeNetwork(){assertThrows(IllegalArgumentException.class,()->gateway.console("memories","../tasks","Bearer opaque-token",Map.of()));assertEquals(0,calls.get());}
 @Test void rejectsUnknownConsoleResourceBeforeNetwork(){assertThrows(IllegalArgumentException.class,()->gateway.console("secrets",null,"Bearer opaque-token",Map.of()));assertEquals(0,calls.get());}
 @Test void rejectsConsoleUnknownQueryBeforeNetwork(){assertThrows(IllegalArgumentException.class,()->gateway.console("users",null,"Bearer opaque-token",Map.of("url","https://attacker")));assertEquals(0,calls.get());}
 @Test void rejectsConsoleLongSelectorBeforeNetwork(){assertThrows(IllegalArgumentException.class,()->gateway.console("users",null,"Bearer opaque-token",Map.of("user_id","x".repeat(257))));assertEquals(0,calls.get());}
 @Test void forwardsConsoleBusinessFilters(){assertEquals("ok",gateway.console("diagnostics",null,"Bearer opaque-token",Map.of("state","failed","flow","recall")).path("status").asText());assertEquals(1,calls.get());}
 @Test void forwardsBoundedMemoryListOptions(){assertEquals("ok",gateway.console("memories",null,"Bearer opaque-token",Map.of("user_id","demo_user_a","include_summary","false","collapse_duplicates","true")).path("status").asText());assertEquals(1,calls.get());}
}
