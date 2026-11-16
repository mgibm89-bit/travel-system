// Backend-ийн хаяг: хуудсыг ямар хаягаар нээсэн (location.hostname) тэр серверийн 8000 порт.
// Жишээ: http://54.12.34.56 гэж нээвэл -> http://54.12.34.56:8000
// Файлыг шууд (file://) нээвэл hostname хоосон байдаг тул localhost ашиглана.
const API_URL = `http://${location.hostname || "localhost"}:8000`;
