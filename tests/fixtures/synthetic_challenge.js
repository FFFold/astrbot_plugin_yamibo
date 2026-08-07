// 合成挑战脚本：仅模拟真实挑战的调用模式（Function 构造、window/document 引用、Math.random、document.cookie 写入），
// 用于单元测试求解器，不含真实混淆代码。
!function () {
  var enc = [110, 111, 120, 95, 106, 115, 116, 95, 118, 49];
  var name = '';
  for (var i = 0; i < enc.length; i++) name += String.fromCharCode(enc[i]);
  var random = String(Math.floor(Math.random() * 10000));
  var payload = 'synthetic_' + random + '_' + String(Date.now());
  var doc = window.document;
  doc.cookie = name + '=2.0_' + random + '_' + payload + ';path=/';
  var fn = Function('', 'return 42;');
  fn();
}();
