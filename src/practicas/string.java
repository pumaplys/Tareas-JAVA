package practicas;

public class string {
    public static void main(String[] args) {

        String cadena = "hola";
        char caracter = cadena.charAt(0);
        System.out.println(caracter);
        System.out.println(caracter + 2);
        System.out.println((char) (caracter + 2));


        String nuevaCadena = "";

        for (int i = 0; i < cadena.length(); i++) {
            char c = cadena.charAt(i);
            c += 2;
            nuevaCadena = nuevaCadena + c;
        }
        System.out.println(nuevaCadena);

    }
}
