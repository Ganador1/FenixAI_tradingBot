/**
 * Input validation utilities
 */

export interface ValidationResult {
  isValid: boolean;
  error?: string;
}

export const validators = {
  email: (email: string): ValidationResult => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(email)) {
      return { isValid: false, error: 'Invalid email format' };
    }
    return { isValid: true };
  },

  password: (password: string): ValidationResult => {
    if (password.length < 8) {
      return { isValid: false, error: 'Password must be at least 8 characters' };
    }
    if (!/[A-Z]/.test(password)) {
      return { isValid: false, error: 'Password must contain uppercase letter' };
    }
    if (!/[0-9]/.test(password)) {
      return { isValid: false, error: 'Password must contain number' };
    }
    return { isValid: true };
  },

  username: (username: string): ValidationResult => {
    if (username.length < 3) {
      return { isValid: false, error: 'Username must be at least 3 characters' };
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(username)) {
      return { isValid: false, error: 'Username can only contain letters, numbers, underscores, and hyphens' };
    }
    return { isValid: true };
  },

  number: (value: string, min?: number, max?: number): ValidationResult => {
    const num = parseFloat(value);
    if (isNaN(num)) {
      return { isValid: false, error: 'Must be a valid number' };
    }
    if (min !== undefined && num < min) {
      return { isValid: false, error: `Must be at least ${min}` };
    }
    if (max !== undefined && num > max) {
      return { isValid: false, error: `Must not exceed ${max}` };
    }
    return { isValid: true };
  },

  url: (url: string): ValidationResult => {
    try {
      new URL(url);
      return { isValid: true };
    } catch {
      return { isValid: false, error: 'Invalid URL format' };
    }
  },

  required: (value: string | undefined | null): ValidationResult => {
    if (!value || value.trim().length === 0) {
      return { isValid: false, error: 'This field is required' };
    }
    return { isValid: true };
  },
};

export const validateForm = (
  formData: Record<string, unknown>,
  schema: Record<string, (value: unknown) => ValidationResult>
): Record<string, string> => {
  const errors: Record<string, string> = {};
  
  Object.keys(schema).forEach((field) => {
    const result = schema[field](formData[field]);
    if (!result.isValid && result.error) {
      errors[field] = result.error;
    }
  });

  return errors;
};
