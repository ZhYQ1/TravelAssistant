package com.travelassistant.jev;

import io.agentscope.extensions.judge.jev.ChoiceAnswer;
import io.agentscope.extensions.judge.jev.ChoiceQuestion;
import io.agentscope.extensions.judge.jev.JevClient;
import io.agentscope.extensions.judge.jev.NoulAnswer;
import io.agentscope.extensions.judge.jev.NoulQuestion;
import io.agentscope.extensions.judge.jev.ScoreAnswer;
import io.agentscope.extensions.judge.jev.ScoreQuestion;
import io.agentscope.extensions.judge.jev.SystemOneRequest;
import io.agentscope.extensions.judge.jev.SystemOneResult;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.logging.Level;
import java.util.logging.Logger;
import org.springframework.http.HttpStatus;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.server.ResponseStatusException;

@RestController
@RequestMapping("/v1")
public class JevController {
    private static final Logger LOGGER = Logger.getLogger(JevController.class.getName());
    private static final String ROUTE_QUESTION = "route_type";

    private final JevClient jevClient;

    public JevController(JevClient jevClient) {
        this.jevClient = jevClient;
    }

    @GetMapping("/healthz")
    public Map<String, String> health() {
        return Map.of("status", "ok", "provider", "agentscope-java-jev");
    }

    @PostMapping("/route-choice")
    public ChoiceResponse routeChoice(@RequestBody ChoiceRequest request) {
        validate(request);
        Map<String, Object> choices = new LinkedHashMap<>();
        choices.putAll(request.options());

        SystemOneRequest systemOneRequest = SystemOneRequest.builder()
                .state(request.utterance())
                .question(
                        ROUTE_QUESTION,
                        new ChoiceQuestion(
                                "用户的差旅请求最适合由哪类服务处理？",
                                choices))
                .build();

        try {
            SystemOneResult result = jevClient.systemOneBlocking(systemOneRequest);
            Object rawAnswer = result.answers().get(ROUTE_QUESTION);
            if (!(rawAnswer instanceof ChoiceAnswer answer)) {
                throw new IllegalStateException("Jev response did not contain a ChoiceAnswer.");
            }
            if (!request.options().containsKey(answer.choice())) {
                throw new IllegalStateException("Jev selected an option not present in the request.");
            }
            if (answer.confidence() == null
                    || !Double.isFinite(answer.confidence())
                    || answer.confidence() < 0.0
                    || answer.confidence() > 1.0) {
                throw new IllegalStateException("Jev returned an invalid confidence value.");
            }
            if (answer.probabilities() == null
                    || !answer.probabilities().keySet().equals(request.options().keySet())
                    || answer.probabilities().values().stream()
                            .anyMatch(probability -> probability == null
                                    || !Double.isFinite(probability)
                                    || probability < 0.0
                                    || probability > 1.0)) {
                throw new IllegalStateException("Jev returned an invalid choice probability distribution.");
            }
            return new ChoiceResponse(
                    answer.choice(),
                    answer.confidence(),
                    answer.probabilities());
        } catch (RuntimeException exception) {
            LOGGER.log(Level.SEVERE, "Official Jev route decision failed.", exception);
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "Official Jev route decision failed.", exception);
        }
    }

    @PostMapping("/noul")
    public NoulResponse noul(@RequestBody NoulRequest request) {
        validate(request);
        SystemOneRequest systemOneRequest = SystemOneRequest.builder()
                .state(request.state())
                .question(
                        "decision",
                        new NoulQuestion(
                                request.instructions(),
                                new NoulQuestion.NoulCriteria(
                                        request.trueMeaning(), request.falseMeaning())))
                .build();

        try {
            SystemOneResult result = jevClient.systemOneBlocking(systemOneRequest);
            Object rawAnswer = result.answers().get("decision");
            if (!(rawAnswer instanceof NoulAnswer answer)
                    || !isProbability(answer.noul())) {
                throw new IllegalStateException("Jev response did not contain a valid NoulAnswer.");
            }
            return new NoulResponse(answer.noul());
        } catch (RuntimeException exception) {
            LOGGER.log(Level.SEVERE, "Official Jev Noul decision failed.", exception);
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "Official Jev Noul decision failed.", exception);
        }
    }

    @PostMapping("/score")
    public ScoreResponse score(@RequestBody ScoreRequest request) {
        validate(request);
        List<Object> criteria = request.criteria().stream()
                .map(level -> Map.<String, Object>of(
                        "name", level.name(),
                        "description", level.description()))
                .map(item -> (Object) item)
                .toList();
        SystemOneRequest systemOneRequest = SystemOneRequest.builder()
                .state(request.state())
                .question(
                        "score",
                        new ScoreQuestion(request.instructions(), criteria))
                .build();

        try {
            SystemOneResult result = jevClient.systemOneBlocking(systemOneRequest);
            Object rawAnswer = result.answers().get("score");
            if (!(rawAnswer instanceof ScoreAnswer answer)
                    || !isFinite(answer.score())
                    || !isProbability(answer.confidence())
                    || !validDistribution(answer.legend(), answer.probabilities())) {
                throw new IllegalStateException("Jev response did not contain a valid ScoreAnswer.");
            }
            return new ScoreResponse(
                    answer.score(), answer.legend(), answer.probabilities(), answer.confidence());
        } catch (RuntimeException exception) {
            LOGGER.log(Level.SEVERE, "Official Jev score decision failed.", exception);
            throw new ResponseStatusException(
                    HttpStatus.BAD_GATEWAY, "Official Jev score decision failed.", exception);
        }
    }

    private static void validate(ChoiceRequest request) {
        if (request == null || request.utterance() == null || request.utterance().isBlank()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "utterance is required.");
        }
        if (request.utterance().length() > 500) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "utterance exceeds 500 characters.");
        }
        if (request.options() == null || request.options().isEmpty() || request.options().size() > 255) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "options must contain 1 to 255 choices.");
        }
        Set<String> invalidOptions = request.options().entrySet().stream()
                .filter(entry -> entry.getKey() == null
                        || entry.getKey().isBlank()
                        || entry.getValue() == null
                        || entry.getValue().isBlank())
                .map(Map.Entry::getKey)
                .collect(java.util.stream.Collectors.toSet());
        if (!invalidOptions.isEmpty()) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "options must have non-empty keys and labels.");
        }
    }

    private static void validate(NoulRequest request) {
        if (request == null || blankOrTooLong(request.state(), 4000)
                || blankOrTooLong(request.instructions(), 1000)
                || blankOrTooLong(request.trueMeaning(), 500)
                || blankOrTooLong(request.falseMeaning(), 500)) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "state, instructions, trueMeaning, and falseMeaning are required and length-limited.");
        }
    }

    private static void validate(ScoreRequest request) {
        if (request == null || blankOrTooLong(request.state(), 4000)
                || blankOrTooLong(request.instructions(), 1000)
                || request.criteria() == null
                || request.criteria().size() < 2
                || request.criteria().size() > 10
                || request.criteria().stream().anyMatch(level ->
                        level == null
                                || blankOrTooLong(level.name(), 100)
                                || blankOrTooLong(level.description(), 500))
                || request.criteria().stream().map(ScoreLevel::name).distinct().count()
                        != request.criteria().size()) {
            throw new ResponseStatusException(
                    HttpStatus.BAD_REQUEST,
                    "state and instructions are required; criteria must have 2 to 10 unique named levels.");
        }
    }

    private static boolean blankOrTooLong(String value, int maxLength) {
        return value == null || value.isBlank() || value.length() > maxLength;
    }

    private static boolean isFinite(Double value) {
        return value != null && Double.isFinite(value);
    }

    private static boolean isProbability(Double value) {
        return isFinite(value) && value >= 0.0 && value <= 1.0;
    }

    private static boolean validDistribution(
            Map<String, String> legend, Map<String, Double> probabilities) {
        if (legend == null
                || probabilities == null
                || !legend.keySet().equals(probabilities.keySet())
                || probabilities.isEmpty()
                || probabilities.values().stream().anyMatch(probability -> !isProbability(probability))) {
            return false;
        }
        double sum = probabilities.values().stream().mapToDouble(Double::doubleValue).sum();
        return Math.abs(sum - 1.0) <= 0.000001 * probabilities.size();
    }

    public record ChoiceRequest(String utterance, Map<String, String> options) {}

    public record ChoiceResponse(
            String choice,
            double confidence,
            Map<String, Double> probabilities) {}

    public record NoulRequest(
            String state, String instructions, String trueMeaning, String falseMeaning) {}

    public record NoulResponse(double noul) {}

    public record ScoreLevel(String name, String description) {}

    public record ScoreRequest(
            String state, String instructions, List<ScoreLevel> criteria) {}

    public record ScoreResponse(
            double score,
            Map<String, String> legend,
            Map<String, Double> probabilities,
            double confidence) {}
}
