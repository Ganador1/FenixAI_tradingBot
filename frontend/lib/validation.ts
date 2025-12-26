/**
 * Validation utilities for forms
 */

export type ValidationRule = (value: any) => string | null;

export type ValidationRules = Record<string, ValidationRule>;

/**
 * Validate a form with given rules
 */
export function validateForm(
  data: Record<string, any>,
  rules: ValidationRules
): Record<string, string> {
  const errors: Record<string, string> = {};

  for (const [field, rule] of Object.entries(rules)) {
    const error = rule(data[field]);
    if (error) {
      errors[field] = error;
    }
  }

  return errors;
}

/**
 * Common validators
 */
export const validators = {
  required: (value: any): string | null => {
    if (value === null || value === undefined || value === '') {
      return 'This field is required';
    }
    return null;
  },

  email: (value: string): string | null => {
    if (!value) {
      return 'Email is required';
    }
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(value)) {
      return 'Please enter a valid email address';
    }
    return null;
  },

  minLength: (min: number) => (value: string): string | null => {
    if (!value || value.length < min) {
      return `Must be at least ${min} characters`;
    }
    return null;
  },

  maxLength: (max: number) => (value: string): string | null => {
    if (value && value.length > max) {
      return `Must be no more than ${max} characters`;
    }
    return null;
  },

  password: (value: string): string | null => {
    if (!value) {
      return 'Password is required';
    }
    if (value.length < 8) {
      return 'Password must be at least 8 characters';
    }
    return null;
  },

  confirmPassword: (password: string) => (value: string): string | null => {
    if (value !== password) {
      return 'Passwords do not match';
    }
    return null;
  },

  number: (value: any): string | null => {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    if (isNaN(Number(value))) {
      return 'Must be a valid number';
    }
    return null;
  },

  positiveNumber: (value: any): string | null => {
    const numError = validators.number(value);
    if (numError) return numError;
    if (Number(value) <= 0) {
      return 'Must be a positive number';
    }
    return null;
  },

  min: (min: number) => (value: any): string | null => {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    if (Number(value) < min) {
      return `Must be at least ${min}`;
    }
    return null;
  },

  max: (max: number) => (value: any): string | null => {
    if (value === null || value === undefined || value === '') {
      return null;
    }
    if (Number(value) > max) {
      return `Must be no more than ${max}`;
    }
    return null;
  },

  pattern: (regex: RegExp, message: string) => (value: string): string | null => {
    if (!value) return null;
    if (!regex.test(value)) {
      return message;
    }
    return null;
  },
};

/**
 * Combine multiple validators
 */
export function combineValidators(...validators: ValidationRule[]): ValidationRule {
  return (value: any) => {
    for (const validator of validators) {
      const error = validator(value);
      if (error) return error;
    }
    return null;
  };
}
