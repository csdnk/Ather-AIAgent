package cn.iocoder.yudao.module.pay.framework.pay.core.client.impl;

import cn.hutool.core.io.IoUtil;
import cn.hutool.core.util.RandomUtil;
import cn.iocoder.yudao.module.pay.enums.PayChannelEnum;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.PayClient;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.dto.order.PayOrderUnifiedReqDTO;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.PayClientFactoryImpl;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.alipay.AlipayPayClientConfig;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.alipay.AlipayQrPayClient;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.alipay.AlipayWapPayClient;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.weixin.WxPayClientConfig;
import cn.iocoder.yudao.module.pay.framework.pay.core.client.impl.weixin.WxPubPayClient;
import org.junit.jupiter.api.Disabled;
import org.junit.jupiter.api.Test;

import java.io.FileInputStream;
import java.io.FileNotFoundException;

/**
 * {@link PayClientFactoryImpl} 的集成测试
 *
 * @author 芋道源码
 */
@Disabled
public class PayClientFactoryImplIntegrationTest {

    private static final String SERVER_URL_SANDBOX = "https://openapi.alipaydev.com/gateway.do";

    private final PayClientFactoryImpl payClientFactory = new PayClientFactoryImpl();

    /**
     * {@link WxPubPayClient} 的 V2 版本
     */
    @Test
    public void testCreatePayClient_WX_PUB_V2() {
        // 创建配置
        WxPayClientConfig config = new WxPayClientConfig();
        config.setAppId("wx041349c6f39b268b");
        config.setMchId("1545083881");
        config.setApiVersion(WxPayClientConfig.API_VERSION_V2);
        config.setMchKey("0alL64UDQdlCwiKZ73ib7ypaIjMns06p");
        // 创建客户端
        Long channelId = RandomUtil.randomLong();
        payClientFactory.createOrUpdatePayClient(channelId, PayChannelEnum.WX_PUB.getCode(), config);
        PayClient<?> client = payClientFactory.getPayClient(channelId);
        // 发起支付
        PayOrderUnifiedReqDTO reqDTO = buildPayOrderUnifiedReqDTO();
//        CommonResult<?> result = client.unifiedOrder(reqDTO);
//        System.out.println(result);
    }

    /**
     * {@link WxPubPayClient} 的 V3 版本
     */
    @Test
    public void testCreatePayClient_WX_PUB_V3() throws FileNotFoundException {
        // 创建配置
        WxPayClientConfig config = new WxPayClientConfig();
        config.setAppId("wx041349c6f39b268b");
        config.setMchId("1545083881");
        config.setApiVersion(WxPayClientConfig.API_VERSION_V3);
        config.setPrivateKeyContent(IoUtil.readUtf8(new FileInputStream("/Users/yunai/Downloads/wx_pay/apiclient_key.pem")));
//        config.setPrivateCertContent(IoUtil.readUtf8(new FileInputStream("/Users/yunai/Downloads/wx_pay/apiclient_cert.pem")));
        config.setApiV3Key("joerVi8y5DJ3o4ttA0o1uH47Xz1u2Ase");
        // 创建客户端
        Long channelId = RandomUtil.randomLong();
        payClientFactory.createOrUpdatePayClient(channelId, PayChannelEnum.WX_PUB.getCode(), config);
        PayClient<?> client = payClientFactory.getPayClient(channelId);
        // 发起支付
        PayOrderUnifiedReqDTO reqDTO = buildPayOrderUnifiedReqDTO();
//        CommonResult<?> result = client.unifiedOrder(reqDTO);
//        System.out.println(result);
    }

    /**
     * {@link AlipayQrPayClient}
     */
    @Test
    @SuppressWarnings("unchecked")
    public void testCreatePayClient_ALIPAY_QR() {
        // 创建配置
        AlipayPayClientConfig config = new AlipayPayClientConfig();
        config.setAppId("2021000118634035");
        config.setServerUrl(SERVER_URL_SANDBOX);
        config.setSignType(AlipayPayClientConfig.SIGN_TYPE_DEFAULT);
        config.setPrivateKey(System.getenv("RUOYI_TEST_PRIVATEKEY"));
        config.setAlipayPublicKey("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnq90KnF4dTnlzzmxpujbI05OYqi5WxAS6cL0gnZFv2gK51HExF8v/BaP7P979PhFMgWTqmOOI+Dtno5s+yD09XTY1WkshbLk6i4g2Xlr8fyW9ODnkU88RI2w9UdPhQU4cPPwBNlrsYhKkVK2OxwM3kFqjoBBY0CZoZCsSQ3LDH5WeZqPArlsS6xa2zqJBuuoKjMrdpELl3eXSjP8K54eDJCbeetCZNKWLL3DPahTPB7LZikfYmslb0QUvCgGapD0xkS7eVq70NaL1G57MWABs4tbfWgxike4Daj3EfUrzIVspQxj7w8HEj9WozJPgL88kSJSits0pqD3n5r8HSuseQIDAQAB");
        // 创建客户端
        Long channelId = RandomUtil.randomLong();
        payClientFactory.createOrUpdatePayClient(channelId, PayChannelEnum.ALIPAY_QR.getCode(), config);
        PayClient<?> client = payClientFactory.getPayClient(channelId);
        // 发起支付
        PayOrderUnifiedReqDTO reqDTO = buildPayOrderUnifiedReqDTO();
        reqDTO.setNotifyUrl("http://yunai.natapp1.cc/admin-api/pay/notify/callback/18"); // TODO @tina: 这里改成你的 natapp 回调地址
//        CommonResult<AlipayTradePrecreateResponse> result = (CommonResult<AlipayTradePrecreateResponse>) client.unifiedOrder(reqDTO);
//        System.out.println(JsonUtils.toJsonString(result));
//        System.out.println(result.getData().getQrCode());
    }

    /**
     * {@link AlipayWapPayClient}
     */
    @Test
    public void testCreatePayClient_ALIPAY_WAP() {
        // 创建配置
        AlipayPayClientConfig config = new AlipayPayClientConfig();
        config.setAppId("2021000118634035");
        config.setServerUrl(SERVER_URL_SANDBOX);
        config.setSignType(AlipayPayClientConfig.SIGN_TYPE_DEFAULT);
        config.setPrivateKey(System.getenv("RUOYI_TEST_PRIVATEKEY"));
        config.setAlipayPublicKey("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAnq90KnF4dTnlzzmxpujbI05OYqi5WxAS6cL0gnZFv2gK51HExF8v/BaP7P979PhFMgWTqmOOI+Dtno5s+yD09XTY1WkshbLk6i4g2Xlr8fyW9ODnkU88RI2w9UdPhQU4cPPwBNlrsYhKkVK2OxwM3kFqjoBBY0CZoZCsSQ3LDH5WeZqPArlsS6xa2zqJBuuoKjMrdpELl3eXSjP8K54eDJCbeetCZNKWLL3DPahTPB7LZikfYmslb0QUvCgGapD0xkS7eVq70NaL1G57MWABs4tbfWgxike4Daj3EfUrzIVspQxj7w8HEj9WozJPgL88kSJSits0pqD3n5r8HSuseQIDAQAB");
        // 创建客户端
        Long channelId = RandomUtil.randomLong();
        payClientFactory.createOrUpdatePayClient(channelId, PayChannelEnum.ALIPAY_WAP.getCode(), config);
        PayClient<?> client = payClientFactory.getPayClient(channelId);
        // 发起支付
        PayOrderUnifiedReqDTO reqDTO = buildPayOrderUnifiedReqDTO();
//        CommonResult<?> result = client.unifiedOrder(reqDTO);
//        System.out.println(JsonUtils.toJsonString(result));
    }

    private static PayOrderUnifiedReqDTO buildPayOrderUnifiedReqDTO() {
        PayOrderUnifiedReqDTO reqDTO = new PayOrderUnifiedReqDTO();
        reqDTO.setPrice(123);
        reqDTO.setSubject("IPhone 13");
        reqDTO.setBody("biubiubiu");
        reqDTO.setOutTradeNo(String.valueOf(System.currentTimeMillis()));
        reqDTO.setUserIp("127.0.0.1");
        reqDTO.setNotifyUrl("http://127.0.0.1:8080");
        return reqDTO;
    }

}
