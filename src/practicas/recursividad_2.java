package practicas;

public class recursividad_2 {
    public static void main(String[] args) {
        String hola = "Hola";
        System.out.println(hola.charAt(0));
        System.out.println(hola.substring(1));

        cadenilla(hola);
    }

    static void cadenilla(String cadena) {
        System.out.println(cadena.charAt(0));
        String subcadena = cadena.substring(1);
        if (subcadena.length() > 0) {
            cadenilla(subcadena);
        }
    }
}
