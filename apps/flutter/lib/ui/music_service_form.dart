import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../models/music_service.dart';

/// Renders Music Assistant config entries as a simple form.
class MusicServiceForm extends StatefulWidget {
  const MusicServiceForm({
    super.key,
    required this.fields,
    this.errors = const {},
    this.onSubmit,
    this.submitLabel = 'Save',
    this.busy = false,
  });

  final List<ConfigField> fields;
  final Map<String, String> errors;
  final Future<void> Function(Map<String, dynamic> values)? onSubmit;
  final String submitLabel;
  final bool busy;

  @override
  State<MusicServiceForm> createState() => MusicServiceFormState();
}

class MusicServiceFormState extends State<MusicServiceForm> {
  final _formKey = GlobalKey<FormState>();
  late Map<String, dynamic> _values;
  var _showAdvanced = false;

  @override
  void initState() {
    super.initState();
    _values = {
      for (final field in widget.fields)
        if (!field.secret)
          field.key: field.value ?? field.defaultValue,
    };
  }

  Map<String, dynamic> collected() {
    return Map<String, dynamic>.from(_values);
  }

  List<ConfigField> get _visible {
    return [
      for (final field in widget.fields)
        if (!field.hidden && (!field.advanced || _showAdvanced)) field,
    ];
  }

  Future<void> _submit() async {
    if (!(_formKey.currentState?.validate() ?? false)) {
      return;
    }
    await widget.onSubmit?.call(collected());
  }

  @override
  Widget build(BuildContext context) {
    final hasAdvanced = widget.fields.any((field) => field.advanced && !field.hidden);
    return Form(
      key: _formKey,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          if (widget.errors['base'] != null)
            Padding(
              padding: const EdgeInsets.only(bottom: 12),
              child: Text(widget.errors['base']!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
            ),
          for (final field in _visible) ...[
            _buildField(field),
            const SizedBox(height: 12),
          ],
          if (hasAdvanced)
            TextButton(
              onPressed: () => setState(() => _showAdvanced = !_showAdvanced),
              child: Text(_showAdvanced ? 'Hide advanced' : 'Show advanced'),
            ),
          if (widget.onSubmit != null)
            FilledButton(
              key: const Key('music-service-submit'),
              onPressed: widget.busy ? null : _submit,
              child: Text(widget.submitLabel),
            ),
        ],
      ),
    );
  }

  Widget _buildField(ConfigField field) {
    final scheme = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final error = widget.errors[field.key];
    if (field.type == 'label' || field.type == 'divider' || field.type == 'alert') {
      return Padding(
        padding: const EdgeInsets.only(top: 4),
        child: Text(field.label ?? field.description ?? '', style: text.bodyMedium),
      );
    }
    if (field.type == 'boolean') {
      final value = _values[field.key] == true;
      return SwitchListTile(
        key: Key('field-${field.key}'),
        contentPadding: EdgeInsets.zero,
        title: Text(field.label ?? field.key),
        subtitle: field.description != null ? Text(field.description!) : null,
        value: value,
        onChanged: field.readOnly || widget.busy
            ? null
            : (next) => setState(() => _values[field.key] = next),
      );
    }
    if (field.type == 'dropdown') {
      return DropdownButtonFormField<dynamic>(
        key: Key('field-${field.key}'),
        // ignore: deprecated_member_use
        value: _values[field.key],
        decoration: InputDecoration(
          labelText: field.label ?? field.key,
          helperText: field.description,
          errorText: error,
          border: const OutlineInputBorder(),
        ),
        items: [
          for (final option in field.options)
            DropdownMenuItem(value: option.value, enabled: !option.disabled, child: Text(option.title)),
        ],
        onChanged: field.readOnly || widget.busy ? null : (value) => setState(() => _values[field.key] = value),
        validator: (value) {
          if (field.requiredField && (value == null || value == '')) {
            return 'Required';
          }
          return error;
        },
      );
    }
    final isInt = field.type == 'integer';
    final isSecret = field.secret || field.type == 'secure_string';
    return TextFormField(
      key: Key('field-${field.key}'),
      enabled: !field.readOnly && !widget.busy,
      initialValue: isSecret ? '' : '${_values[field.key] ?? ''}',
      obscureText: isSecret,
      keyboardType: isInt ? TextInputType.number : TextInputType.text,
      inputFormatters: isInt ? [FilteringTextInputFormatter.digitsOnly] : null,
      decoration: InputDecoration(
        labelText: field.label ?? field.key,
        helperText: isSecret && field.hasValue ? 'Leave blank to keep the saved value' : field.description,
        errorText: error,
        border: const OutlineInputBorder(),
        suffixIcon: isSecret ? Icon(Icons.lock_outline, color: scheme.onSurfaceVariant) : null,
      ),
      onChanged: (value) {
        if (isInt) {
          _values[field.key] = int.tryParse(value);
        } else if (isSecret && value.isEmpty) {
          _values.remove(field.key);
        } else {
          _values[field.key] = value;
        }
      },
      validator: (value) {
        if (field.requiredField && !isSecret && (value == null || value.isEmpty)) {
          return 'Required';
        }
        if (field.requiredField && isSecret && (value == null || value.isEmpty) && !field.hasValue) {
          return 'Required';
        }
        return null;
      },
    );
  }
}
