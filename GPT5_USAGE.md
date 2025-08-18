# GPT-5 Support in Hercules

This document explains how to use GPT-5 models (gpt-5, gpt-5-mini, gpt-5-nano) with the Hercules framework.

## Overview

GPT-5 models use `max_completion_tokens` instead of `max_tokens` for controlling response length. The Hercules framework automatically handles this parameter conversion based on the model family.

## Supported GPT-5 Models

- `gpt-5` - Full GPT-5 model
- `gpt-5-mini` - Smaller, faster GPT-5 variant
- `gpt-5-nano` - Lightweight GPT-5 variant
- `gpt-5-2025-08-07` - Specific GPT-5 version
- `gpt-5-mini-2025-08-07` - Specific GPT-5-mini version
- `gpt-5-nano-2025-08-07` - Specific GPT-5-nano version

## Configuration Methods

### 1. Environment Variables

```bash
# Set the model name
export LLM_MODEL_NAME=gpt-5

# Use max_completion_tokens for GPT-5 models
export LLM_MODEL_MAX_COMPLETION_TOKENS=4096

# Other parameters
export LLM_MODEL_TEMPERATURE=0.0
export LLM_MODEL_SEED=12345
```

### 2. Configuration File

Create or update your `agents_llm_config.json`:

```json
{
  "openai_gpt5": {
    "planner_agent": {
      "model_name": "gpt-5",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 4096,
        "seed": 12345
      }
    },
    "nav_agent": {
      "model_name": "gpt-5-mini",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 2048
      }
    },
    "mem_agent": {
      "model_name": "gpt-5-nano",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 1024
      }
    },
    "helper_agent": {
      "model_name": "gpt-5",
      "model_api_key": "your-api-key",
      "model_api_type": "openai",
      "llm_config_params": {
        "temperature": 0.0,
        "max_completion_tokens": 4096
      }
    }
  }
}
```

Then set the environment variable to use this configuration:

```bash
export AGENTS_LLM_CONFIG_FILE=./agents_llm_config.json
export AGENTS_LLM_CONFIG_FILE_REF_KEY=openai_gpt5
```

## Automatic Parameter Conversion

The framework automatically converts parameters based on the model family:

- **GPT-5 models**: `max_tokens` → `max_completion_tokens`
- **Claude models**: `max_tokens` → `max_tokens_to_sample`
- **Other models**: `max_completion_tokens` → `max_tokens` (if needed)

### Example Conversions

```python
# Input config for GPT-5
config = {
    "max_tokens": 2048,  # This will be converted
    "temperature": 0.1
}

# After adaptation for gpt-5 model
result = {
    "max_completion_tokens": 2048,  # Converted from max_tokens
    "temperature": 0.1,
    "model": "gpt-5"
}
```

## Best Practices

### 1. Use Appropriate Token Limits

- **gpt-5**: Up to 128,000 tokens context, 4,096 completion tokens
- **gpt-5-mini**: Up to 128,000 tokens context, 2,048 completion tokens
- **gpt-5-nano**: Up to 128,000 tokens context, 1,024 completion tokens

### 2. Error Handling

The framework will log warnings when converting deprecated parameters:

```
WARNING: Deprecated param 'max_tokens' supplied for gpt-5; auto-translating to 'max_completion_tokens'.
```




## Testing

Run the test script to verify GPT-5 support:

```bash
cd testzeus-hercules
python test_gpt5_support.py
```

## Troubleshooting

### Common Issues

1. **Parameter not recognized**: Ensure you're using `max_completion_tokens` for GPT-5 models
2. **Conversion errors**: Check that the model name starts with the correct prefix
3. **API errors**: Verify your OpenAI API key has access to GPT-5 models

### Debug Mode

Enable debug logging to see parameter conversions:

```bash
export LOG_LEVEL=DEBUG
```

## Migration from GPT-4

If you're migrating from GPT-4 to GPT-5:

1. Update model names in your configuration
2. Replace `max_tokens` with `max_completion_tokens`
3. Test with smaller token limits first
4. Verify your OpenAI API key has access to GPT-5 models



## Support

For issues with GPT-5 support:

1. Check the logs for parameter conversion messages
2. Verify your configuration format
3. Test with the provided test script
4. Review the main documentation for additional details
