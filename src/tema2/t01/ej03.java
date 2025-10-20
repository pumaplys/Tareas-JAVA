package tema2.t01;

import java.util.Scanner;

public class ej03 {
    public static void main(String[] args) {
        Scanner sc=new Scanner(System.in);

        System.out.println("Dias de la semana:");
        System.out.println("1. Lunes");
        System.out.println("2. Martes");
        System.out.println("3. Miercoles");
        System.out.println("4. Jueves");
        System.out.println("5. Viernes");
        System.out.println("6. Sabado");
        System.out.println("7. Domingo");
        int n=sc.nextInt();

        switch(n){
            case 1:
                System.out.println("Elegiste el Lunes");
                break;
            case 2:
                System.out.println("Elegiste el Martes");
                break;
            case 3:
                System.out.println("Elegiste el Miercoles");
                break;
            case 4:
                System.out.println("Elegiste el Jueves");
                break;
            case 5:
                System.out.println("Elegiste el Viernes");
                break;
            case 6:
                System.out.println("Elegiste el Sabado");
                break;
            case 7:
                System.out.println("Elegiste el Domingo");
                break;
            default:
                System.out.println("No existe un numero de la semana con lo que introduciste");
                break;
        }
    }
}
