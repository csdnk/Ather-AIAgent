package cn.iocoder.yudao.module.system.aether;

import cn.iocoder.yudao.framework.common.exception.ServiceException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.*;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;

/** Fixed-origin, allowlisted operations bridge. No cookies, tenant headers, redirects or retries. */
@Component
public class AetherOpsGateway {
 public static final Set<String> RESOURCES=Set.of("overview","requests","tasks","memories","incidents","configuration","backups","usage","resources","support","commands","audit","rules","quotas");
 private static final Set<String> QUERY=Set.of("limit","offset","status","request_id","task_id","command_id","cursor","q","from","to");
 private static final Set<String> CONSOLE_RESOURCES=Set.of("users","conversations","memories","recalls","diagnostics","tasks","history");
 private static final Set<String> CONSOLE_DETAILS=Set.of("conversations","memories","recalls","tasks");
 private static final Set<String> CONSOLE_QUERY=Set.of("limit","offset","cursor","q","user_id","tenant_id","flow","state","kind","status","from","to");
 private final String origin; private final ObjectMapper json;
 private final HttpClient http=HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).followRedirects(HttpClient.Redirect.NEVER).build();
 public AetherOpsGateway(@Value("${aether.ops.base-url:http://aether-python:8000}") String origin,ObjectMapper json){
  URI u=URI.create(origin);
  if(!Set.of("http","https").contains(u.getScheme())||u.getHost()==null||u.getUserInfo()!=null||u.getQuery()!=null||u.getFragment()!=null||!(u.getPath().isEmpty()||u.getPath().equals("/")))throw new IllegalArgumentException("Operations origin must contain scheme and host only");
  this.origin=origin.replaceAll("/$","");this.json=json;
 }
 public JsonNode read(String resource,String bearer,Map<String,String> query){
  if(!RESOURCES.contains(resource))throw new IllegalArgumentException("Unknown operations resource");
  if(!QUERY.containsAll(query.keySet())||query.size()>10||query.entrySet().stream().anyMatch(e->e.getValue().length()>(e.getKey().equals("cursor")?8192:256)))throw new IllegalArgumentException("Unsupported query parameter");
  String suffix=query.isEmpty()?"":"?"+query.entrySet().stream().map(e->encode(e.getKey())+"="+encode(e.getValue())).collect(Collectors.joining("&"));
  return exchange(resource+suffix,bearer,null);
 }
 public JsonNode command(String bearer,JsonNode command){
  if(!command.isObject()||!command.path("command_id").asText().matches("[A-Za-z0-9_-]{8,128}")||!RESOURCES.contains(command.path("resource").asText())||!command.path("action").asText().matches("[a-z][a-z0-9_]{1,63}"))throw new IllegalArgumentException("Command requires a stable command_id, resource and action");
  return exchange("commands",bearer,command);
 }
 /** Target selectors are query data only. Python/P3 authorize them using the real caller. */
 public JsonNode console(String resource,String id,String bearer,Map<String,String> query){
  if(!CONSOLE_RESOURCES.contains(resource)||(id!=null&&(!CONSOLE_DETAILS.contains(resource)||!id.matches("[A-Za-z0-9_-]{1,128}"))))throw new IllegalArgumentException("Unknown console resource");
  if(!CONSOLE_QUERY.containsAll(query.keySet())||query.size()>12||query.entrySet().stream().anyMatch(e->e.getValue()==null||e.getValue().length()>(e.getKey().equals("cursor")?4096:256)))throw new IllegalArgumentException("Unsupported console query");
  String suffix=query.isEmpty()?"":"?"+query.entrySet().stream().map(e->encode(e.getKey())+"="+encode(e.getValue())).collect(Collectors.joining("&"));
  return exchange("console/"+resource+(id==null?"":"/"+id)+suffix,bearer,null);
 }
 private JsonNode exchange(String path,String bearer,JsonNode body){
  if(bearer==null||!bearer.matches("Bearer [^\\s]{8,4096}"))throw new IllegalArgumentException("Bearer token required");
  var req=HttpRequest.newBuilder(URI.create(origin+"/platform-ops/v1/"+path)).timeout(Duration.ofSeconds(25)).header("Authorization",bearer).header("Accept","application/json");
  if(body==null)req.GET();else req.header("Content-Type","application/json").POST(HttpRequest.BodyPublishers.ofString(body.toString()));
  try{
   var response=http.send(req.build(),HttpResponse.BodyHandlers.ofString());
   if(response.statusCode()<200||response.statusCode()>=300){
    String code="upstream_error";
    try{var e=json.readTree(response.body());code=e.path("detail").path("code").asText(e.path("error").asText("upstream_error"));}catch(Exception ignored){}
    if(!code.matches("[a-zA-Z0-9_-]{1,80}"))code="upstream_error";
    throw new ServiceException(response.statusCode(),"Aether operations: "+code);
   }
   if(response.body().length()>4_000_000)throw new ServiceException(502,"Operations response too large");
   return json.readTree(response.body());
  }catch(ServiceException e){throw e;}catch(InterruptedException e){Thread.currentThread().interrupt();throw new ServiceException(503,"Operations interrupted; query original command_id before retry");}
  catch(Exception e){throw new ServiceException(503,body==null?"Operations source unavailable":"Command outcome unknown; query original command_id before retry");}
 }
 private static String encode(String text){return URLEncoder.encode(text,StandardCharsets.UTF_8);}
}
