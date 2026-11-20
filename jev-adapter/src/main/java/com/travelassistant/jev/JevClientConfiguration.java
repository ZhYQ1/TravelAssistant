package com.travelassistant.jev;

import io.agentscope.extensions.judge.jev.JevClient;
import io.agentscope.extensions.judge.jev.JevRetryPolicy;
import java.time.Duration;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class JevClientConfiguration {
    @Bean
    JevClient jevClient(
            @Value("${agentscope.jev.base-url}") String baseUrl,
            @Value("${agentscope.jev.model}") String model,
            @Value("${agentscope.jev.timeout-seconds}") long timeoutSeconds) {
        String apiKey = firstNonBlank(
                System.getenv("TYPESAFE_API_KEY"),
                System.getenv("JEV_API_KEY"));
        if (apiKey == null) {
            throw new IllegalStateException(
                    "Set TYPESAFE_API_KEY (or JEV_API_KEY) before starting the Jev adapter.");
        }

        return JevClient.builder()
                .apiKey(apiKey)
                .baseUrl(baseUrl)
                .model(model)
                .timeout(Duration.ofSeconds(timeoutSeconds))
                .retryPolicy(new JevRetryPolicy(2, Duration.ofMillis(500)))
                .build();
    }

    private static String firstNonBlank(String preferred, String fallback) {
        if (preferred != null && !preferred.isBlank()) {
            return preferred;
        }
        if (fallback != null && !fallback.isBlank()) {
            return fallback;
        }
        return null;
    }
}
